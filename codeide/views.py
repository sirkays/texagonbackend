# app: ide/views.py
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.response import Response
from rest_framework import status

from rest_framework_api_key.permissions import HasAPIKey
from api.authentication import SessionTokenAuthentication  # your existing session-token auth

from core.utils import _get_student_for_user, _get_teacher_for_user
from learning.models import Lesson
from .models import CodeSnippet, CodeSubmission, CodeComment
from .serializers import (
    CodeSnippetSerializer,
    CodeSubmissionSerializer,
    TeacherUpdateSubmissionSerializer,
    CodeCommentSerializer,
)
from .utils import user_is_submission_student, user_teaches_lesson, user_is_teacher


# ---------- SNIPPETS ----------
@api_view(["POST"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def snippet_create(request):
    """
    Student: save code (draft).
    Body: {lesson: <id|nullable>, title, language, code_text, meta?}
    """
    student = _get_student_for_user(request.user)
    if not student:
        return Response({"detail": "Student profile not found."}, status=403)

    data = request.data.copy()
    data.pop("student", None)

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
