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

from .models import CodeSnippet, CodeSubmission, CodeComment, CodeFile, Folder
from .serializers import (
    CodeSnippetSerializer,
    CodeSubmissionSerializer,
    TeacherUpdateSubmissionSerializer,
    CodeCommentSerializer,
    CodeFileSerializer,
    SubmissionListSerializer,
    TeacherCodeSubmissionDetailSerializer,
    TeacherCodeCommentSerializer,
    StudentUpdateSubmissionSerializer,
    FolderSerializer,
)
from .utils import user_is_submission_student, user_teaches_lesson, user_is_teacher
from api.permissions import RequiresActiveStudentSubscription
from django.db.models.functions import Lower, Trim

logger = logging.getLogger(__name__)


# ===========================================================================
# FOLDERS
# ===========================================================================
@api_view(["GET"])
@permission_classes([HasAPIKey & RequiresActiveStudentSubscription()])
@authentication_classes([SessionTokenAuthentication])
def folder_list(request):
    """
    List all folders for the current student. The frontend builds the tree
    from `parent` ids; we return a flat list because that's the cheapest
    payload and the client already manages tree state.
    """
    student = _get_student_for_user(request.user)
    if not student:
        return Response({"detail": "Student profile not found."}, status=403)

    qs = Folder.objects.filter(student=student).select_related("parent")
    parent_id = request.query_params.get("parent")
    if parent_id == "null" or parent_id == "":
        qs = qs.filter(parent__isnull=True)
    elif parent_id:
        qs = qs.filter(parent_id=parent_id)

    return Response(FolderSerializer(qs, many=True).data)


@api_view(["POST"])
@permission_classes([HasAPIKey & RequiresActiveStudentSubscription()])
@authentication_classes([SessionTokenAuthentication])
def folder_create(request):
    """
    Create a new folder. Body: { name, parent (optional) }.
    """
    student = _get_student_for_user(request.user)
    if not student:
        return Response({"detail": "Student profile not found."}, status=403)

    name = (request.data.get("name") or "").strip()
    parent_id = request.data.get("parent")

    if not name:
        return Response({"detail": "Folder name is required."}, status=400)
    if "/" in name or "\\" in name:
        return Response({"detail": "Folder name cannot contain '/' or '\\'."}, status=400)

    parent = None
    if parent_id:
        try:
            parent = Folder.objects.get(pk=parent_id, student=student)
        except Folder.DoesNotExist:
            return Response({"detail": "Parent folder not found."}, status=404)

    # Enforce uniqueness (student, parent, name) at the app layer for a clean error msg
    if Folder.objects.filter(student=student, parent=parent, name=name).exists():
        return Response(
            {"detail": "A folder with this name already exists here."},
            status=409,
        )

    folder = Folder.objects.create(student=student, parent=parent, name=name)
    return Response(FolderSerializer(folder).data, status=201)


@api_view(["PATCH"])
@permission_classes([HasAPIKey & RequiresActiveStudentSubscription()])
@authentication_classes([SessionTokenAuthentication])
def folder_update(request, folder_id: int):
    """
    Rename or move a folder. Body: { name?, parent? }
    """
    student = _get_student_for_user(request.user)
    if not student:
        return Response({"detail": "Student profile not found."}, status=403)

    folder = get_object_or_404(Folder, pk=folder_id, student=student)

    new_name = request.data.get("name")
    if new_name is not None:
        new_name = new_name.strip()
        if not new_name:
            return Response({"detail": "Folder name is required."}, status=400)
        if "/" in new_name or "\\" in new_name:
            return Response({"detail": "Folder name cannot contain '/' or '\\'."}, status=400)
        folder.name = new_name

    if "parent" in request.data:
        parent_id = request.data.get("parent")
        if parent_id in (None, "", "null"):
            folder.parent = None
        else:
            try:
                new_parent = Folder.objects.get(pk=parent_id, student=student)
            except Folder.DoesNotExist:
                return Response({"detail": "Parent folder not found."}, status=404)
            # Prevent cycles: walk up from new_parent and ensure folder is not an ancestor
            cursor = new_parent
            for _ in range(64):
                if cursor is None:
                    break
                if cursor.id == folder.id:
                    return Response(
                        {"detail": "Cannot move a folder into itself or its descendant."},
                        status=400,
                    )
                cursor = cursor.parent
            folder.parent = new_parent

    # Uniqueness check on save
    sibling_qs = Folder.objects.filter(
        student=student, parent=folder.parent, name=folder.name
    ).exclude(pk=folder.pk)
    if sibling_qs.exists():
        return Response(
            {"detail": "A folder with this name already exists here."},
            status=409,
        )

    folder.save()
    return Response(FolderSerializer(folder).data)


@api_view(["DELETE"])
@permission_classes([HasAPIKey & RequiresActiveStudentSubscription()])
@authentication_classes([SessionTokenAuthentication])
def folder_delete(request, folder_id: int):
    """
    Delete a folder. By default we only allow deleting empty folders.
    Pass ?force=1 to recursively delete contents (snippets get deleted,
    files get their stored blobs removed too).
    """
    student = _get_student_for_user(request.user)
    if not student:
        return Response({"detail": "Student profile not found."}, status=403)

    folder = get_object_or_404(Folder, pk=folder_id, student=student)
    force = request.query_params.get("force") in ("1", "true", "True")

    if not force:
        has_children = folder.children.exists()
        has_snippets = folder.snippets.exists()
        has_files = folder.files.exists()
        if has_children or has_snippets or has_files:
            return Response(
                {"detail": "Folder is not empty. Pass ?force=1 to delete with contents."},
                status=400,
            )
        folder.delete()
        return Response(status=204)

    # Force: recursively delete. Walk the subtree and clean up file blobs first.
    def _collect_descendants(root: Folder):
        result = [root]
        stack = [root]
        while stack:
            node = stack.pop()
            for child in node.children.all():
                result.append(child)
                stack.append(child)
        return result

    descendants = _collect_descendants(folder)
    descendant_ids = [f.id for f in descendants]

    # Delete file blobs from storage explicitly (CASCADE won't remove S3 objects)
    for f in CodeFile.objects.filter(student=student, folder_id__in=descendant_ids):
        try:
            f.file.delete(save=False)
        except Exception:
            logger.exception("Failed to delete file blob during folder cascade", extra={"file_id": f.id})

    # CASCADE on Folder.parent removes children; SET_NULL on snippets/files
    # would orphan them — but we want them gone, so delete explicitly first.
    CodeSnippet.objects.filter(student=student, folder_id__in=descendant_ids).delete()
    CodeFile.objects.filter(student=student, folder_id__in=descendant_ids).delete()
    folder.delete()
    return Response(status=204)


# ===========================================================================
# SNIPPETS
# ===========================================================================
@api_view(["DELETE"])
@permission_classes([HasAPIKey & RequiresActiveStudentSubscription()])
@authentication_classes([SessionTokenAuthentication])
def snippet_delete(request, snippet_id: int):
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
@permission_classes([HasAPIKey & RequiresActiveStudentSubscription()])
@authentication_classes([SessionTokenAuthentication])
def snippet_create(request):
    """
    Create or update a code snippet for the current student.

    Behavior (issue #4 — saving a file that already exists should update it):
    - If `id` / `pk` is provided → update that specific snippet.
    - Else, look up an existing snippet by (student, title, language, folder).
      If found → update it. If not → create a new one.
    """
    student = _get_student_for_user(request.user)
    if not student:
        return Response({"detail": "Student profile not found."}, status=403)

    data = request.data.copy()
    data.pop("student", None)

    snippet_id = data.pop("id", None) or data.pop("pk", None)

    # Resolve folder if provided (ensure it belongs to this student)
    folder_id = data.get("folder")
    folder = None
    if folder_id not in (None, "", 0, "0"):
        try:
            folder = Folder.objects.get(pk=folder_id, student=student)
        except Folder.DoesNotExist:
            return Response({"detail": "Folder not found."}, status=404)
    # Normalize folder field on the payload
    data["folder"] = folder.id if folder else None

    # ----- Explicit update by id -----
    if snippet_id:
        try:
            snippet = CodeSnippet.objects.get(pk=snippet_id, student=student)
        except CodeSnippet.DoesNotExist:
            return Response({"detail": "Snippet not found."}, status=404)

        serializer = CodeSnippetSerializer(snippet, data=data, partial=True)
        if serializer.is_valid():
            obj = serializer.save()
            return Response(CodeSnippetSerializer(obj).data, status=200)
        return Response(serializer.errors, status=400)

    # ----- No id: try to find an existing snippet to update (dedupe) -----
    title = (data.get("title") or "").strip()
    language = data.get("language") or ""

    if title and language:
        existing = (
            CodeSnippet.objects
            .filter(
                student=student,
                title__iexact=title,
                language=language,
                folder=folder,
            )
            .order_by("-updated_at")
            .first()
        )
        if existing:
            serializer = CodeSnippetSerializer(existing, data=data, partial=True)
            if serializer.is_valid():
                obj = serializer.save()
                return Response(CodeSnippetSerializer(obj).data, status=200)
            return Response(serializer.errors, status=400)

    # ----- Create new -----
    serializer = CodeSnippetSerializer(data=data)
    if serializer.is_valid():
        obj = serializer.save(student=student)
        return Response(CodeSnippetSerializer(obj).data, status=201)
    return Response(serializer.errors, status=400)


@api_view(["GET"])
@permission_classes([HasAPIKey & RequiresActiveStudentSubscription()])
@authentication_classes([SessionTokenAuthentication])
def snippet_list(request):
    """
    Student: list saved code.
    Optional filters: ?lesson=<id>, ?folder=<id|null>
    """
    student = _get_student_for_user(request.user)
    if not student:
        return Response({"detail": "Student profile not found."}, status=403)

    qs = CodeSnippet.objects.filter(student=student)
    lesson_id = request.query_params.get("lesson")
    if lesson_id:
        qs = qs.filter(lesson_id=lesson_id)

    folder_id = request.query_params.get("folder")
    if folder_id == "null" or folder_id == "":
        # Explicit "no folder" filter — only honor if the param is actually present
        if "folder" in request.query_params:
            qs = qs.filter(folder__isnull=True)
    elif folder_id:
        qs = qs.filter(folder_id=folder_id)

    return Response(CodeSnippetSerializer(qs, many=True).data)


@api_view(["GET"])
@permission_classes([HasAPIKey & RequiresActiveStudentSubscription()])
@authentication_classes([SessionTokenAuthentication])
def snippet_detail(request, snippet_id: int):
    student = _get_student_for_user(request.user)
    if not student:
        return Response({"detail": "Student profile not found."}, status=403)

    obj = get_object_or_404(CodeSnippet, id=snippet_id, student=student)
    return Response(CodeSnippetSerializer(obj).data)


# ===========================================================================
# SUBMISSIONS
# ===========================================================================
@api_view(["POST"])
@permission_classes([HasAPIKey & RequiresActiveStudentSubscription()])
@authentication_classes([SessionTokenAuthentication])
def submission_create(request):
    student = _get_student_for_user(request.user)
    if not student:
        return Response({"detail": "Student profile not found."}, status=403)

    title = (request.data.get("title") or "").strip() or None
    lesson_id = request.data.get("lesson")
    language = request.data.get("language")
    code_text = request.data.get("code_text")

    if not lesson_id or not language or not code_text:
        return Response({"detail": "lesson, language and code_text are required."}, status=400)
    lesson = get_object_or_404(Lesson, id=lesson_id)
    submission = CodeSubmission.objects.create(
        title=title,
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

    if submission.lesson.module.course.course_type == "public":
        transaction.on_commit(_log_after_commit)

    return Response(CodeSubmissionSerializer(submission).data, status=201)


@api_view(["GET"])
@permission_classes([HasAPIKey & RequiresActiveStudentSubscription()])
@authentication_classes([SessionTokenAuthentication])
def submission_detail(request, submission_id: int):
    submission = get_object_or_404(CodeSubmission, id=submission_id)

    if not (user_is_submission_student(request.user, submission) or user_teaches_lesson(request.user, submission.lesson)):
        return Response({"detail": "Not allowed."}, status=403)
    return Response(CodeSubmissionSerializer(submission).data)


@api_view(["PATCH"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def submission_teacher_update(request, submission_id: int):
    submission = get_object_or_404(CodeSubmission, id=submission_id)

    if not user_teaches_lesson(request.user, submission.lesson):
        return Response({"detail": "Only the course teacher can update/grade."}, status=403)

    teacher = _get_teacher_for_user(request.user)
    if not teacher:
        return Response({"detail": "Teacher profile not found."}, status=403)

    serializer = TeacherUpdateSubmissionSerializer(submission, data=request.data, partial=True)
    if serializer.is_valid():
        instance = serializer.save()
        if any(k in request.data for k in ["score", "feedback", "correction_code"]):
            instance.graded_by = teacher
            instance.graded_at = timezone.now()
            if not instance.status or instance.status == CodeSubmission.Status.SUBMITTED:
                instance.status = CodeSubmission.Status.GRADED
        instance.save()
        return Response(CodeSubmissionSerializer(instance).data)
    return Response(serializer.errors, status=400)


# ===========================================================================
# COMMENTS
# ===========================================================================
@api_view(["POST"])
@permission_classes([HasAPIKey & RequiresActiveStudentSubscription()])
@authentication_classes([SessionTokenAuthentication])
def submission_comment_create(request, submission_id: int):
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


# ===========================================================================
# CODE FILES
# ===========================================================================
MAX_UPLOAD_MB = getattr(settings, "IDE_MAX_UPLOAD_MB", 25)
ALLOWED_EXTENSIONS = getattr(
    settings,
    "IDE_ALLOWED_EXTENSIONS",
    {"py", "txt", "csv", "json", "xml", "yaml", "yml", "md", "html", "css", "js", "ts", "jsx", "tsx", "java", "kt", "c", "cpp", "h", "hpp", "cs", "go", "rs", "php", "sql", "sh", "ipynb", "png", "jpg", "jpeg", "gif", "svg", "pdf"}
)


def _ext_ok(name: str) -> bool:
    import os
    ext = os.path.splitext(name)[1].lower().lstrip(".")
    return (not ALLOWED_EXTENSIONS) or (ext in ALLOWED_EXTENSIONS)


@api_view(["POST"])
@permission_classes([HasAPIKey & RequiresActiveStudentSubscription()])
@authentication_classes([SessionTokenAuthentication])
def codefile_upload(request):
    """
    Student: upload a file for IDE (multipart/form-data).
    Fields: file (required), lesson (optional), folder (optional), label (optional)
    """
    student = _get_student_for_user(request.user)
    if not student:
        return Response({"detail": "Student profile not found."}, status=403)

    up = request.FILES.get("file")
    if not up:
        return Response({"detail": "file field is required (multipart/form-data)."}, status=400)

    if up.size > MAX_UPLOAD_MB * 1024 * 1024:
        return Response({"detail": f"File too large. Limit is {MAX_UPLOAD_MB} MB."}, status=413)

    if not _ext_ok(up.name):
        return Response({"detail": "File type not allowed."}, status=400)

    lesson = None
    lesson_id = request.data.get("lesson")
    if lesson_id:
        lesson = get_object_or_404(Lesson, id=lesson_id)

    folder = None
    folder_id = request.data.get("folder")
    if folder_id not in (None, "", 0, "0"):
        try:
            folder = Folder.objects.get(pk=folder_id, student=student)
        except Folder.DoesNotExist:
            return Response({"detail": "Folder not found."}, status=404)

    obj = CodeFile.objects.create(
        student=student,
        lesson=lesson,
        folder=folder,
        label=(request.data.get("label") or "").strip()[:255],
        file=up,
        original_name=up.name[:255],
        content_type=getattr(up, "content_type", "") or "",
        size_bytes=up.size or 0,
    )
    return Response(CodeFileSerializer(obj, context={"request": request}).data, status=201)


@api_view(["GET"])
@permission_classes([HasAPIKey & RequiresActiveStudentSubscription()])
@authentication_classes([SessionTokenAuthentication])
def codefile_list(request):
    """
    Student: list own files. Optional filters: ?lesson=<id>, ?folder=<id|null>
    """
    student = _get_student_for_user(request.user)
    if not student:
        return Response({"detail": "Student profile not found."}, status=403)

    qs = CodeFile.objects.filter(student=student)
    lesson_id = request.query_params.get("lesson")
    if lesson_id:
        qs = qs.filter(lesson_id=lesson_id)

    if "folder" in request.query_params:
        folder_id = request.query_params.get("folder")
        if folder_id in ("null", ""):
            qs = qs.filter(folder__isnull=True)
        elif folder_id:
            qs = qs.filter(folder_id=folder_id)

    return Response(CodeFileSerializer(qs, many=True, context={"request": request}).data)


@api_view(["GET", "PATCH"])
@permission_classes([HasAPIKey & RequiresActiveStudentSubscription()])
@authentication_classes([SessionTokenAuthentication])
def codefile_detail(request, file_id: int):
    """
    GET: return file metadata.
    PATCH: student-only — update the file's label or folder.
    """
    obj = get_object_or_404(CodeFile, id=file_id)
    student = _get_student_for_user(request.user)

    if request.method == "GET":
        if student and obj.student_id == student.id:
            return Response(CodeFileSerializer(obj, context={"request": request}).data)
        if obj.lesson and user_teaches_lesson(request.user, obj.lesson):
            return Response(CodeFileSerializer(obj, context={"request": request}).data)
        return Response({"detail": "Not allowed."}, status=403)

    # PATCH
    if not student or obj.student_id != student.id:
        return Response({"detail": "Not allowed."}, status=403)

    label = request.data.get("label")
    if label is not None:
        obj.label = (label or "").strip()[:255]

    if "folder" in request.data:
        folder_id = request.data.get("folder")
        if folder_id in (None, "", "null", 0, "0"):
            obj.folder = None
        else:
            try:
                obj.folder = Folder.objects.get(pk=folder_id, student=student)
            except Folder.DoesNotExist:
                return Response({"detail": "Folder not found."}, status=404)

    obj.save(update_fields=["label", "folder", "updated_at"])
    return Response(CodeFileSerializer(obj, context={"request": request}).data)


@api_view(["DELETE"])
@permission_classes([HasAPIKey & RequiresActiveStudentSubscription()])
@authentication_classes([SessionTokenAuthentication])
def codefile_delete(request, file_id: int):
    student = _get_student_for_user(request.user)
    if not student:
        return Response({"detail": "Student profile not found."}, status=403)

    obj = get_object_or_404(CodeFile, id=file_id, student=student)

    obj.file.delete(save=False)
    obj.delete()
    return Response(status=204)


# ===========================================================================
# TEACHER VIEWS  (unchanged)
# ===========================================================================
class QuickPagination(pagination.PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


def _teacher_scoped_queryset(request):
    teacher = _get_teacher_for_user(request.user)
    if not teacher:
        return None, Response({"detail": "Teacher profile not found."}, status=403)

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
    try:
        qs = qs.filter(lesson__module__course__organization=org)
    except Exception:
        pass

    return qs, None


def _apply_filters(request, qs):
    q = request.query_params.get("search", "").strip()
    course_id = request.query_params.get("course_id")
    lesson_id = request.query_params.get("lesson_id")
    class_id = request.query_params.get("class_id") or request.query_params.get("classroom_id")
    status_value = request.query_params.get("status", "").strip()

    if q:
        qs = qs.filter(
            Q(title__icontains=q) |
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


@api_view(["GET"])
@permission_classes([HasAPIKey, IsAuthenticated])
@authentication_classes([SessionTokenAuthentication])
def teacher_submissions_list(request):
    qs, err = _teacher_scoped_queryset(request)
    if err:
        return err

    qs = _apply_filters(request, qs)
    qs = qs.annotate(
        title_norm=Lower(Trim("title"))
    ).exclude(title_norm="")

    qs = (
        qs.order_by("student_id", "lesson_id", "title_norm", "-created_at", "-id")
        .distinct("student_id", "lesson_id", "title_norm")
    )
    paginator = QuickPagination()
    page = paginator.paginate_queryset(qs, request)
    ser = SubmissionListSerializer(page, many=True)
    return paginator.get_paginated_response(ser.data)


@api_view(["GET"])
@permission_classes([HasAPIKey, IsAuthenticated])
@authentication_classes([SessionTokenAuthentication])
def teacher_submission_detail(request, pk: int):
    qs, err = _teacher_scoped_queryset(request)
    if err:
        return err
    obj = get_object_or_404(qs, pk=pk)
    ser = TeacherCodeSubmissionDetailSerializer(obj, context={"request": request})
    return Response(ser.data)


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


@api_view(["POST"])
@permission_classes([HasAPIKey, IsAuthenticated])
@authentication_classes([SessionTokenAuthentication])
def teacher_submission_grade(request, pk: int):
    qs, err = _teacher_scoped_queryset(request)
    if err:
        return err
    submission = get_object_or_404(qs, pk=pk)

    was_graded_before = (submission.status == CodeSubmission.Status.GRADED)

    score = request.data.get("score")
    feedback = request.data.get("feedback", "")
    correction_code = request.data.get("correction_code", "")

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

        if submission.lesson.module.course.course_type == "public":
            transaction.on_commit(_log_after_commit)

    return Response(TeacherCodeSubmissionDetailSerializer(submission).data, status=200)


@api_view(["GET"])
@permission_classes([HasAPIKey & RequiresActiveStudentSubscription()])
@authentication_classes([SessionTokenAuthentication])
def student_submission_list(request):
    lesson_id = request.query_params.get("lesson")

    student_profile = _get_student_for_user(request.user)
    qs = CodeSubmission.objects.select_related("lesson", "student").prefetch_related("comments", "comments__author")

    if student_profile:
        qs = qs.filter(student=student_profile)
    else:
        return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)

    if lesson_id:
        try:
            qs = qs.filter(lesson_id=int(lesson_id))
        except ValueError:
            return Response({"detail": "lesson must be an integer."}, status=400)

    qs = qs.order_by("-created_at")

    data = CodeSubmissionSerializer(qs, many=True).data
    return Response(data, status=200)


@api_view(["PATCH"])
@permission_classes([HasAPIKey & RequiresActiveStudentSubscription()])
@authentication_classes([SessionTokenAuthentication])
def student_update_submission(request, submission_id: int):
    student_profile = _get_student_for_user(request.user)
    if not student_profile:
        return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)

    try:
        sub = CodeSubmission.objects.select_related("student", "lesson").get(
            pk=submission_id,
            student=student_profile,
        )
    except CodeSubmission.DoesNotExist:
        return Response({"detail": "Submission not found."}, status=status.HTTP_404_NOT_FOUND)

    ser = StudentUpdateSubmissionSerializer(sub, data=request.data, partial=True)
    ser.is_valid(raise_exception=True)

    updated = ser.save(
        status=CodeSubmission.Status.REVISED,
        score=None,
        feedback="",
        correction_code="",
        graded_by=None,
        graded_at=None,
    )

    return Response(CodeSubmissionSerializer(updated).data, status=status.HTTP_200_OK)


@api_view(["GET"])
@permission_classes([HasAPIKey, IsAuthenticated])
@authentication_classes([SessionTokenAuthentication])
def resolve_upload_by_label(request):
    label = request.query_params.get("label", "").strip()

    if not label:
        return Response(
            {"error": "Missing required query param: label"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    teacher_profile = getattr(request.user, "teacher_profile", None)
    qs = None
    if teacher_profile:
        qs = CodeFile.objects.filter(
            Q(label__iexact=label) | Q(original_name__iexact=label)
        ).order_by("-created_at")
    else:
        student_profile = getattr(request.user, "student_profile", None)
        if student_profile is None:
            return Response(
                {"error": "No student profile associated with this account"},
                status=status.HTTP_403_FORBIDDEN,
            )

        qs = CodeFile.objects.filter(
            student=student_profile
        ).filter(
            Q(label__iexact=label) | Q(original_name__iexact=label)
        ).order_by("-created_at")

    if teacher_profile is None and qs is None:
        return Response(
            {"error": "No teacher profile associated with this account"},
            status=status.HTTP_403_FORBIDDEN,
        )

    file_obj = qs.first()

    if file_obj is None:
        return Response(
            {"error": f'No uploaded file found with label: "{label}"'},
            status=status.HTTP_404_NOT_FOUND,
        )

    file_url = request.build_absolute_uri(file_obj.file.url)

    return Response({
        "id":            file_obj.id,
        "label":         file_obj.label,
        "original_name": file_obj.original_name,
        "url":           file_url,
        "content_type":  file_obj.content_type,
        "size_bytes":    file_obj.size_bytes,
        "lesson":        file_obj.lesson_id,
        "folder":        file_obj.folder_id,
    })

    