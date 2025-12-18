# app: ide/views.py
from django.conf import settings
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from gamification.services.engine import log_event 
from gamification.models import Streak
from rest_framework import status, pagination
from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.db import IntegrityError, transaction
from rest_framework_api_key.permissions import HasAPIKey
from api.authentication import SessionTokenAuthentication
import logging
from core.utils import _get_student_for_user, _get_teacher_for_user, _resolve_org
from learning.models import Lesson

from .models import CodeSnippet, CodeSubmission, CodeComment, CodeFile
from .serializers import (
    CodeSnippetSerializer,
    CodeSubmissionSerializer,
    TeacherUpdateSubmissionSerializer,
    CodeCommentSerializer,
    CodeFileSerializer,
    SubmissionListSerializer,
    TeacherCodeSubmissionDetailSerializer,
    TeacherCodeCommentSerializer,
)
from .utils import user_is_submission_student, user_teaches_lesson, user_is_teacher

logger = logging.getLogger(__name__)
# ---------- SNIPPETS ----------

@api_view(["DELETE"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def snippet_delete(request, snippet_id: int):
    """
    Delete a saved snippet belonging to the current student.
    """
    student = _get_student_for_user(request.user)
    if not student:
        return Response({"detail": "Student profile not found."}, status=403)

    try:
        snippet = CodeSnippet.objects.get(pk=snippet_id, student=student)
    except CodeSnippet.DoesNotExist:
        return Response({"detail": "Snippet not found."}, status=404)

    snippet.delete()
    return Response(status=204)


@api_view(["POST"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def snippet_create(request):
    """
    Create or update a code snippet for the current student.

    - If no `id` / `pk` is provided in the body → create a new snippet.
    - If `id` / `pk` is provided → update the existing snippet (for this student).
    """
    student = _get_student_for_user(request.user)
    if not student:
        return Response({"detail": "Student profile not found."}, status=403)

    data = request.data.copy()
    # Never allow student to be overridden from payload
    data.pop("student", None)

    # accept either "id" or "pk"
    snippet_id = data.pop("id", None) or data.pop("pk", None)

    if snippet_id:
        # -------- UPDATE FLOW --------
        try:
            snippet = CodeSnippet.objects.get(pk=snippet_id, student=student)
        except CodeSnippet.DoesNotExist:
            return Response({"detail": "Snippet not found."}, status=404)

        # partial update: they can send only some fields
        serializer = CodeSnippetSerializer(snippet, data=data, partial=True)
        if serializer.is_valid():
            obj = serializer.save()  # student already set on instance
            return Response(CodeSnippetSerializer(obj).data, status=200)
        return Response(serializer.errors, status=400)

    # -------- CREATE FLOW (no id/pk) --------
    serializer = CodeSnippetSerializer(data=data)
    if serializer.is_valid():
        obj = serializer.save(student=student)
        return Response(CodeSnippetSerializer(obj).data, status=201)
    return Response(serializer.errors, status=400)

@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def snippet_list(request):
    """
    Student: list saved code (optionally filter by lesson).
    Query: ?lesson=<id>
    """
    student = _get_student_for_user(request.user)
    if not student:
        return Response({"detail": "Student profile not found."}, status=403)

    qs = CodeSnippet.objects.filter(student=student)
    lesson_id = request.query_params.get("lesson")
    if lesson_id:
        qs = qs.filter(lesson_id=lesson_id)

    return Response(CodeSnippetSerializer(qs, many=True).data)


@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def snippet_detail(request, snippet_id: int):
    """
    Student: fetch saved code detail.
    """
    student = _get_student_for_user(request.user)
    if not student:
        return Response({"detail": "Student profile not found."}, status=403)

    obj = get_object_or_404(CodeSnippet, id=snippet_id, student=student)
    return Response(CodeSnippetSerializer(obj).data)


# ---------- SUBMISSIONS ----------
@api_view(["POST"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def submission_create(request):
    """
    Student: submit code by lesson.
    Body: {lesson: id, language, code_text}
    """
    student = _get_student_for_user(request.user)
    if not student:
        return Response({"detail": "Student profile not found."}, status=403)

    lesson_id = request.data.get("lesson")
    language = request.data.get("language")
    code_text = request.data.get("code_text")

    if not lesson_id or not language or not code_text:
        return Response({"detail": "lesson, language and code_text are required."}, status=400)

    lesson = get_object_or_404(Lesson, id=lesson_id)

    submission = CodeSubmission.objects.create(
        student=student,
        lesson=lesson,
        language=language,
        code_text=code_text,
        status=CodeSubmission.Status.SUBMITTED,
    )
    org = getattr(student, "organization", None)
    today = timezone.localdate()
    def _log_after_commit():
        try:
            log_event(
                student=student,
                org=org,
                event_type="exercise_submitted",
                value=1,
                meta={
                    "submission_id": submission.id,
                    "lesson_id": lesson.id,
                    "language": language,
                },
                dedupe_key=f"exercise_submitted:{submission.id}",
            )

            log_event(
                student=student,
                org=org,
                event_type="daily_active",
                value=1,
                meta={"source": "submission_create", "submission_id": submission.id},
                dedupe_key=f"daily_active:{student.id}:{today.isoformat()}",
            )

        except Exception:
            logger.exception(
                "Gamification on_commit failed (non-fatal)",
                extra={"submission_id": submission.id, "lesson_id": lesson.id},
            )
            return


    transaction.on_commit(_log_after_commit)

    return Response(CodeSubmissionSerializer(submission).data, status=201)


@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def submission_detail(request, submission_id: int):
    """
    Student or course teacher: fetch submission detail (with comments).
    """
    submission = get_object_or_404(CodeSubmission, id=submission_id)

    # permission: owner student OR the lesson's teacher
    if not (user_is_submission_student(request.user, submission) or user_teaches_lesson(request.user, submission.lesson)):
        return Response({"detail": "Not allowed."}, status=403)

    return Response(CodeSubmissionSerializer(submission).data)


@api_view(["PATCH"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def submission_teacher_update(request, submission_id: int):
    """
    Teacher: grade code or make corrections (update submission fields).
    Body (any subset):
      - code_text (optional, to supply a corrected version yourself)
      - score (decimal), feedback (text), correction_code (text), status ("graded" or "revised")
    """
    submission = get_object_or_404(CodeSubmission, id=submission_id)

    # must be this lesson's teacher
    if not user_teaches_lesson(request.user, submission.lesson):
        return Response({"detail": "Only the course teacher can update/grade."}, status=403)

    teacher = _get_teacher_for_user(request.user)
    if not teacher:
        return Response({"detail": "Teacher profile not found."}, status=403)

    serializer = TeacherUpdateSubmissionSerializer(submission, data=request.data, partial=True)
    if serializer.is_valid():
        instance = serializer.save()
        # mark graded fields when score/feedback/correction provided
        if any(k in request.data for k in ["score", "feedback", "correction_code"]):
            instance.graded_by = teacher
            instance.graded_at = timezone.now()
            if not instance.status or instance.status == CodeSubmission.Status.SUBMITTED:
                instance.status = CodeSubmission.Status.GRADED
        instance.save()
        return Response(CodeSubmissionSerializer(instance).data)
    return Response(serializer.errors, status=400)


# ---------- COMMENTS ----------
@api_view(["POST"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def submission_comment_create(request, submission_id: int):
    """
    Student (owner) or Teacher (of the lesson) can comment on a submission.
    Body: {message}
    """
    submission = get_object_or_404(CodeSubmission, id=submission_id)
    can_student = user_is_submission_student(request.user, submission)
    can_teacher = user_teaches_lesson(request.user, submission.lesson)

    if not (can_student or can_teacher):
        return Response({"detail": "Not allowed."}, status=403)

    msg = (request.data.get("message") or "").strip()
    if not msg:
        return Response({"detail": "message is required."}, status=400)

    role = "teacher" if can_teacher else "student"
    comment = CodeComment.objects.create(
        submission=submission,
        author=request.user,
        author_role=role,
        message=msg,
    )
    return Response(CodeCommentSerializer(comment).data, status=201)





MAX_UPLOAD_MB = getattr(settings, "IDE_MAX_UPLOAD_MB", 25)  # override in settings if you like
ALLOWED_EXTENSIONS = getattr(
    settings,
    "IDE_ALLOWED_EXTENSIONS",
    # keep liberal; IDE often needs these
    {"py", "txt", "csv", "json", "xml", "yaml", "yml", "md", "html", "css", "js", "ts", "jsx", "tsx", "java", "kt", "c", "cpp", "h", "hpp", "cs", "go", "rs", "php", "sql", "sh", "ipynb", "png", "jpg", "jpeg", "gif", "svg", "pdf"}
)

def _ext_ok(name: str) -> bool:
    import os
    ext = os.path.splitext(name)[1].lower().lstrip(".")
    return (not ALLOWED_EXTENSIONS) or (ext in ALLOWED_EXTENSIONS)

# -------- FILES --------
@api_view(["POST"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def codefile_upload(request):
    """
    Student: upload a file for IDE.
    Content-Type: multipart/form-data
      - file: (required) binary content
      - lesson: <id|nullable>
      - label: short description (optional)
    Response: CodeFileSerializer (includes .url)
    """
    parser_classes = (MultiPartParser, FormParser)  # DRF respects view.attr too
    for p in parser_classes:
        pass  # attribute marker; not used programmatically

    student = _get_student_for_user(request.user)
    if not student:
        return Response({"detail": "Student profile not found."}, status=403)

    up = request.FILES.get("file")
    if not up:
        return Response({"detail": "file field is required (multipart/form-data)."}, status=400)

    # size check
    if up.size > MAX_UPLOAD_MB * 1024 * 1024:
        return Response({"detail": f"File too large. Limit is {MAX_UPLOAD_MB} MB."}, status=413)

    if not _ext_ok(up.name):
        return Response({"detail": "File type not allowed."}, status=400)

    # lesson (optional)
    lesson = None
    lesson_id = request.data.get("lesson")
    if lesson_id:
        lesson = get_object_or_404(Lesson, id=lesson_id)

    obj = CodeFile.objects.create(
        student=student,
        lesson=lesson,
        label=(request.data.get("label") or "").strip()[:255],
        file=up,
        original_name=up.name[:255],
        content_type=getattr(up, "content_type", "") or "",
        size_bytes=up.size or 0,
    )
    return Response(CodeFileSerializer(obj, context={"request": request}).data, status=201)


@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def codefile_list(request):
    """
    Student: list own files (optional: filter by lesson).
    Teachers: if ?lesson=<id> and user teaches that lesson, will list those students' files? -> NO.
    Only the authenticated student can see their own files. Teachers can only view by direct id if teaches the lesson.
    Query: ?lesson=<id>
    """
    student = _get_student_for_user(request.user)
    if not student:
        return Response({"detail": "Student profile not found."}, status=403)

    qs = CodeFile.objects.filter(student=student)
    lesson_id = request.query_params.get("lesson")
    if lesson_id:
        qs = qs.filter(lesson_id=lesson_id)
    return Response(CodeFileSerializer(qs, many=True, context={"request": request}).data)


# ide/views.py
@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def codefile_detail(request, file_id: int):
    """
    Student: fetch own file metadata.
    Teacher: can view metadata if they teach the attached lesson (if any).
    """
    obj = get_object_or_404(CodeFile, id=file_id)

    # owner student can view
    student = _get_student_for_user(request.user)
    if student and obj.student_id == student.id:
        return Response(CodeFileSerializer(obj, context={"request": request}).data)

    # teacher can read only if the file is tied to a lesson they teach
    if obj.lesson and user_teaches_lesson(request.user, obj.lesson):
        return Response(CodeFileSerializer(obj, context={"request": request}).data)

    return Response({"detail": "Not allowed."}, status=403)


@api_view(["DELETE"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def codefile_delete(request, file_id: int):
    """
    Student: delete own file (removes both DB row and stored file).
    Teachers: cannot delete student files.
    """
    student = _get_student_for_user(request.user)
    if not student:
        return Response({"detail": "Student profile not found."}, status=403)

    obj = get_object_or_404(CodeFile, id=file_id, student=student)

    # delete removes file from storage as well
    obj.file.delete(save=False)
    obj.delete()
    return Response(status=204)





# ---------- Pagination ----------
class QuickPagination(pagination.PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100

# ---------- Helpers ----------
def _teacher_scoped_queryset(request):
    """
    Limit submissions to courses taught by the authenticated teacher
    and (optionally) to the selected organization.
    """
    teacher = _get_teacher_for_user(request.user)
    if not teacher:
        return None, Response({"detail": "Teacher profile not found."}, status=403)

    # Optional org resolution & enforcement
    org, org_err = _resolve_org(request)
    if org_err:
        return None, org_err

    qs = (
        CodeSubmission.objects
        .select_related(
            "student__user",
            "lesson__module__course__teacher__user",
            "lesson__module__course__classroom",
        )
        .prefetch_related("comments")
        .filter(lesson__module__course__teacher=teacher)
    )
    # If your Course has organization, also scope to org
    try:
        qs = qs.filter(lesson__module__course__organization=org)
    except Exception:
        # If no org relation on Course, you can remove this filter
        pass

    return qs, None

def _apply_filters(request, qs):
    """
    Filters:
      - q: text search across student name/email, lesson name, course name
      - class_id, classroom_id
      - course_id
      - lesson_id
      - status (single or CSV)
    """
    q = request.query_params.get("q", "").strip()
    course_id = request.query_params.get("course_id")
    lesson_id = request.query_params.get("lesson_id")
    class_id = request.query_params.get("class_id") or request.query_params.get("classroom_id")
    status_value = request.query_params.get("status", "").strip()

    if q:
        qs = qs.filter(
            Q(lesson__name__icontains=q) |
            Q(lesson__module__course__name__icontains=q) |
            Q(student__user__first_name__icontains=q) |
            Q(student__user__last_name__icontains=q) |
            Q(student__user__email__icontains=q)
        )

    if course_id:
        qs = qs.filter(lesson__module__course_id=course_id)

    if lesson_id:
        qs = qs.filter(lesson_id=lesson_id)

    if class_id:
        # Prefer course.classroom
        qs = qs.filter(
            Q(lesson__module__course__classroom_id=class_id)
            | Q(student__classroom_id=class_id)
        )

    if status_value:
        statuses = [s.strip() for s in status_value.split(",") if s.strip()]
        if statuses:
            qs = qs.filter(status__in=statuses)

    order = request.query_params.get("order", "-created_at")
    allowed = {"created_at", "-created_at", "graded_at", "-graded_at", "status", "-status"}
    if order in allowed:
        qs = qs.order_by(order, "-id")

    return qs

# ---------- 1) Teacher list ----------
@api_view(["GET"])
@permission_classes([HasAPIKey, IsAuthenticated])
@authentication_classes([SessionTokenAuthentication])
def teacher_submissions_list(request):
    qs, err = _teacher_scoped_queryset(request)
    if err:
        return err
    qs = _apply_filters(request, qs)

    paginator = QuickPagination()
    page = paginator.paginate_queryset(qs, request)
    ser = SubmissionListSerializer(page, many=True)
    return paginator.get_paginated_response(ser.data)

# ---------- 2) Detail ----------
@api_view(["GET"])
@permission_classes([HasAPIKey, IsAuthenticated])
@authentication_classes([SessionTokenAuthentication])
def teacher_submission_detail(request, pk: int):
    qs, err = _teacher_scoped_queryset(request)
    if err:
        return err
    obj = get_object_or_404(qs, pk=pk)
    ser = TeacherCodeSubmissionDetailSerializer(obj)
    return Response(ser.data)

# ---------- 3) Comments list/create ----------
@api_view(["GET", "POST"])
@permission_classes([HasAPIKey, IsAuthenticated])
@authentication_classes([SessionTokenAuthentication])
def teacher_submission_comments(request, pk: int):
    qs, err = _teacher_scoped_queryset(request)
    if err:
        return err
    submission = get_object_or_404(qs, pk=pk)

    if request.method == "GET":
        comments = submission.comments.select_related("author").order_by("created_at")
        ser = TeacherCodeCommentSerializer(comments, many=True)
        return Response(ser.data)

    # POST: create comment
    message = (request.data.get("message") or "").strip()
    if not message:
        return Response({"detail": "Message is required."}, status=400)

    c = CodeComment.objects.create(
        submission=submission,
        author=request.user,
        author_role="teacher",
        message=message,
    )
    return Response(TeacherCodeCommentSerializer(c).data, status=201)

# ---------- 4) Grade ----------
@api_view(["POST"])
@permission_classes([HasAPIKey, IsAuthenticated])
@authentication_classes([SessionTokenAuthentication])
def teacher_submission_grade(request, pk: int):
    qs, err = _teacher_scoped_queryset(request)
    if err:
        return err
    submission = get_object_or_404(qs, pk=pk)

    was_graded_before = (submission.status == CodeSubmission.Status.GRADED)

    # data
    score = request.data.get("score")
    feedback = request.data.get("feedback", "")
    correction_code = request.data.get("correction_code", "")

    # Basic validation
    try:
        score_val = None if score in (None, "") else float(score)
        if score_val is not None and (score_val < 0 or score_val > 1000):
            return Response({"detail": "Score must be between 0 and 1000."}, status=400)
    except Exception:
        return Response({"detail": "Score must be numeric."}, status=400)

    teacher = _get_teacher_for_user(request.user)
    if not teacher:
        return Response({"detail": "Teacher profile not found."}, status=403)

    submission.score = score_val
    submission.feedback = feedback
    submission.correction_code = correction_code
    submission.status = CodeSubmission.Status.GRADED
    submission.graded_by = teacher
    submission.graded_at = timezone.now()
    submission.save(update_fields=[
        "score", "feedback", "correction_code", "status", "graded_by", "graded_at", "updated_at"
    ])
    # ---------- GAMIFICATION EVENT LOG (GRADE TRANSITION) ----------
    if not was_graded_before:
        student = submission.student
        org = getattr(student, "organization", None)
        today = timezone.localdate()
    def _log_after_commit():
        try:
            log_event(
                student=student,
                org=org,
                event_type="exercise_graded",
                value=1,
                meta={
                    "submission_id": submission.id,
                    "lesson_id": submission.lesson_id,
                    "score": submission.score,
                    "graded_by": getattr(teacher, "id", None),
                },
                dedupe_key=f"exercise_graded:{submission.id}",
            )
            # optional: mastery threshold
            if submission.score is not None and float(submission.score) >= 80:
                log_event(
                    student=student,
                    org=org,
                    event_type="exercise_mastered",
                    value=1,
                    meta={"submission_id": submission.id, "score": submission.score},
                    dedupe_key=f"exercise_mastered:{submission.id}",
                )
            log_event(
                student=student,
                org=org,
                event_type="daily_active",
                value=1,
                meta={"source": "teacher_submission_grade", "submission_id": submission.id},
                dedupe_key=f"daily_active:{student.id}:{today.isoformat()}",
            )
            Streak.set_student_streak(student, org, 'daily_active', 'streak_champion')
        except Exception:
            logger.exception(
                "Gamification on_commit failed (non-fatal)",
                extra={"submission_id": submission.id, "lesson_id": submission.lesson_id},
            )
            return
    if submission.lesson.module.course.course_type == "public":
        transaction.on_commit(_log_after_commit)
    # --------------------------------------------------------------

    return Response(TeacherCodeSubmissionDetailSerializer(submission).data, status=200)
