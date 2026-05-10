# codeide/project_views.py
# New project-based submission views replacing old CodeSubmission views.

import io, re, zipfile, logging
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import status, pagination
from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework_api_key.permissions import HasAPIKey
from api.authentication import SessionTokenAuthentication
from api.permissions import RequiresActiveStudentSubscription
from core.utils import _get_student_for_user, _get_teacher_for_user, _resolve_org
from learning.models import Lesson

from .models import CodeProject, ProjectFile, CodeComment
from .serializers import (
    CodeProjectListSerializer, CodeProjectDetailSerializer,
    StudentProjectSerializer, CodeCommentSerializer,
)

logger = logging.getLogger(__name__)

LANG_EXT_MAP = {
    "js": "javascript", "py": "python", "html": "html", "css": "css",
    "java": "java", "cpp": "cpp", "c": "cpp", "ts": "javascript",
    "jsx": "javascript", "tsx": "javascript",
}

def _detect_language(path: str) -> str:
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return LANG_EXT_MAP.get(ext, "other")


# ── Student: submit project ──────────────────────────────────────────
@api_view(["POST"])
@permission_classes([HasAPIKey & RequiresActiveStudentSubscription()])
@authentication_classes([SessionTokenAuthentication])
def project_submit(request):
    student = _get_student_for_user(request.user)
    if not student:
        return Response({"detail": "Student profile not found."}, status=403)

    title = (request.data.get("title") or "").strip()
    lesson_id = request.data.get("lesson")
    files_data = request.data.get("files", [])

    if not title:
        return Response({"detail": "title is required."}, status=400)
    if not lesson_id:
        return Response({"detail": "lesson is required."}, status=400)
    if not files_data or not isinstance(files_data, list):
        return Response({"detail": "files list is required."}, status=400)

    lesson = get_object_or_404(Lesson, id=lesson_id)

    with transaction.atomic():
        project = CodeProject.objects.create(
            title=title, student=student, lesson=lesson,
            status=CodeProject.Status.SUBMITTED,
        )
        for f in files_data:
            path = (f.get("path") or "").strip()
            if not path:
                continue
            lang = f.get("language") or _detect_language(path)
            ProjectFile.objects.create(
                project=project, path=path, language=lang,
                code_text=f.get("code_text", ""),
            )

    return Response(StudentProjectSerializer(project).data, status=201)


# ── Student: resubmit (revise) project ───────────────────────────────
@api_view(["POST"])
@permission_classes([HasAPIKey & RequiresActiveStudentSubscription()])
@authentication_classes([SessionTokenAuthentication])
def project_resubmit(request, pk: int):
    student = _get_student_for_user(request.user)
    if not student:
        return Response({"detail": "Student profile not found."}, status=403)

    project = get_object_or_404(CodeProject, pk=pk, student=student)
    files_data = request.data.get("files", [])
    title = (request.data.get("title") or "").strip() or project.title

    if not files_data or not isinstance(files_data, list):
        return Response({"detail": "files list is required."}, status=400)

    with transaction.atomic():
        project.title = title
        project.status = CodeProject.Status.REVISED
        project.score = None
        project.feedback = ""
        project.graded_by = None
        project.graded_at = None
        project.save()
        project.files.all().delete()
        for f in files_data:
            path = (f.get("path") or "").strip()
            if not path:
                continue
            lang = f.get("language") or _detect_language(path)
            ProjectFile.objects.create(
                project=project, path=path, language=lang,
                code_text=f.get("code_text", ""),
            )

    project.refresh_from_db()
    return Response(StudentProjectSerializer(project).data, status=200)


# ── Student: list own projects ───────────────────────────────────────
@api_view(["GET"])
@permission_classes([HasAPIKey & RequiresActiveStudentSubscription()])
@authentication_classes([SessionTokenAuthentication])
def student_project_list(request):
    student = _get_student_for_user(request.user)
    if not student:
        return Response({"detail": "Not allowed."}, status=403)

    qs = CodeProject.objects.filter(student=student).prefetch_related("files", "comments__author")
    lesson_id = request.query_params.get("lesson")
    if lesson_id:
        qs = qs.filter(lesson_id=lesson_id)
    qs = qs.order_by("-created_at")
    return Response(StudentProjectSerializer(qs, many=True).data)


# ── Student: single project detail ──────────────────────────────────
@api_view(["GET"])
@permission_classes([HasAPIKey & RequiresActiveStudentSubscription()])
@authentication_classes([SessionTokenAuthentication])
def student_project_detail(request, pk: int):
    student = _get_student_for_user(request.user)
    if not student:
        return Response({"detail": "Not allowed."}, status=403)
    project = get_object_or_404(
        CodeProject.objects.prefetch_related("files", "comments__author"),
        pk=pk, student=student
    )
    return Response(StudentProjectSerializer(project).data)


# ── Teacher helpers ──────────────────────────────────────────────────
class QuickPagination(pagination.PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100

def _teacher_project_qs(request):
    teacher = _get_teacher_for_user(request.user)
    if not teacher:
        return None, Response({"detail": "Teacher profile not found."}, status=403)
    org, org_err = _resolve_org(request)
    if org_err:
        return None, org_err
    qs = (
        CodeProject.objects
        .select_related("student__user", "lesson__module__course__teacher__user",
                        "lesson__module__course__classroom")
        .prefetch_related("files", "comments__author")
        .filter(lesson__module__course__teacher=teacher)
    )
    try:
        qs = qs.filter(lesson__module__course__organization=org)
    except Exception:
        pass
    return qs, None

def _apply_project_filters(request, qs):
    q = request.query_params.get("search", "").strip()
    course_id = request.query_params.get("course_id")
    lesson_id = request.query_params.get("lesson_id")
    class_id = request.query_params.get("class_id") or request.query_params.get("classroom_id")
    status_val = request.query_params.get("status", "").strip()

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
    if status_val:
        statuses = [s.strip() for s in status_val.split(",") if s.strip()]
        if statuses:
            qs = qs.filter(status__in=statuses)

    order = request.query_params.get("order", "-created_at")
    allowed = {"created_at", "-created_at", "graded_at", "-graded_at", "status", "-status"}
    if order in allowed:
        qs = qs.order_by(order, "-id")

    return qs


# ── Teacher: list projects ───────────────────────────────────────────
@api_view(["GET"])
@permission_classes([HasAPIKey, IsAuthenticated])
@authentication_classes([SessionTokenAuthentication])
def teacher_projects_list(request):
    qs, err = _teacher_project_qs(request)
    if err:
        return err
    qs = _apply_project_filters(request, qs)
    paginator = QuickPagination()
    page = paginator.paginate_queryset(qs, request)
    ser = CodeProjectListSerializer(page, many=True)
    return paginator.get_paginated_response(ser.data)


# ── Teacher: project detail ──────────────────────────────────────────
@api_view(["GET"])
@permission_classes([HasAPIKey, IsAuthenticated])
@authentication_classes([SessionTokenAuthentication])
def teacher_project_detail(request, pk: int):
    qs, err = _teacher_project_qs(request)
    if err:
        return err
    obj = get_object_or_404(qs, pk=pk)
    return Response(CodeProjectDetailSerializer(obj, context={"request": request}).data)


# ── Teacher: comments ────────────────────────────────────────────────
@api_view(["GET", "POST"])
@permission_classes([HasAPIKey, IsAuthenticated])
@authentication_classes([SessionTokenAuthentication])
def teacher_project_comments(request, pk: int):
    qs, err = _teacher_project_qs(request)
    if err:
        return err
    project = get_object_or_404(qs, pk=pk)

    if request.method == "GET":
        comments = project.comments.select_related("author").order_by("created_at")
        return Response(CodeCommentSerializer(comments, many=True).data)

    message = (request.data.get("message") or "").strip()
    if not message:
        return Response({"detail": "Message is required."}, status=400)
    c = CodeComment.objects.create(
        project=project, author=request.user, author_role="teacher", message=message,
    )
    return Response(CodeCommentSerializer(c).data, status=201)


# ── Teacher: grade project ───────────────────────────────────────────
@api_view(["POST"])
@permission_classes([HasAPIKey, IsAuthenticated])
@authentication_classes([SessionTokenAuthentication])
def teacher_project_grade(request, pk: int):
    qs, err = _teacher_project_qs(request)
    if err:
        return err
    project = get_object_or_404(qs, pk=pk)

    score = request.data.get("score")
    feedback = request.data.get("feedback", "")
    corrections = request.data.get("corrections", {})

    try:
        score_val = None if score in (None, "") else float(score)
        if score_val is not None and (score_val < 0 or score_val > 1000):
            return Response({"detail": "Score must be between 0 and 1000."}, status=400)
    except Exception:
        return Response({"detail": "Score must be numeric."}, status=400)

    teacher = _get_teacher_for_user(request.user)
    if not teacher:
        return Response({"detail": "Teacher profile not found."}, status=403)

    now = timezone.now()
    project.score = score_val
    project.feedback = feedback
    project.status = CodeProject.Status.GRADED
    project.graded_by = teacher
    project.graded_at = now
    project.save(update_fields=[
        "score", "feedback", "status", "graded_by", "graded_at", "updated_at",
    ])

    if isinstance(corrections, dict) and corrections:
        for file_id_str, corr_code in corrections.items():
            try:
                fid = int(file_id_str)
            except (ValueError, TypeError):
                continue
            ProjectFile.objects.filter(pk=fid, project=project).update(
                correction_code=corr_code or "",
            )

    project.refresh_from_db()
    return Response(CodeProjectDetailSerializer(project).data, status=200)


# ── Teacher: download project as ZIP ─────────────────────────────────
@api_view(["GET"])
@permission_classes([HasAPIKey, IsAuthenticated])
@authentication_classes([SessionTokenAuthentication])
def teacher_project_download(request, pk: int):
    qs, err = _teacher_project_qs(request)
    if err:
        return err
    project = get_object_or_404(qs, pk=pk)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for pf in project.files.all():
            zf.writestr(pf.path, pf.code_text or "")
    buf.seek(0)

    student_user = getattr(project.student, "user", None)
    student_name = ""
    if student_user:
        student_name = (student_user.get_full_name() or student_user.email or "").strip()
    safe = re.sub(r"[^\w\s-]", "", f"{student_name}_{project.title}").strip()
    safe = re.sub(r"\s+", "_", safe) or "download"

    response = HttpResponse(buf.read(), content_type="application/zip")
    response["Content-Disposition"] = f'attachment; filename="{safe}.zip"'
    return response
