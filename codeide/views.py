# codeide/views.py
# Retains: folder, snippet, codefile, and upload-resolve views.
# Project submission/grading views moved to project_views.py.

from django.conf import settings
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework_api_key.permissions import HasAPIKey
from api.authentication import SessionTokenAuthentication
import logging
from core.utils import _get_student_for_user
from learning.models import Lesson

from .models import CodeSnippet, CodeComment, CodeFile, Folder
from .serializers import (
    CodeSnippetSerializer,
    CodeCommentSerializer,
    CodeFileSerializer,
    FolderSerializer,
)
from api.permissions import RequiresActiveStudentSubscription

logger = logging.getLogger(__name__)


# ===========================================================================
# FOLDERS
# ===========================================================================
@api_view(["GET"])
@permission_classes([HasAPIKey & RequiresActiveStudentSubscription()])
@authentication_classes([SessionTokenAuthentication])
def folder_list(request):
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
    if Folder.objects.filter(student=student, parent=parent, name=name).exists():
        return Response({"detail": "A folder with this name already exists here."}, status=409)
    folder = Folder.objects.create(student=student, parent=parent, name=name)
    return Response(FolderSerializer(folder).data, status=201)


@api_view(["PATCH"])
@permission_classes([HasAPIKey & RequiresActiveStudentSubscription()])
@authentication_classes([SessionTokenAuthentication])
def folder_update(request, folder_id: int):
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
            cursor = new_parent
            for _ in range(64):
                if cursor is None:
                    break
                if cursor.id == folder.id:
                    return Response({"detail": "Cannot move a folder into itself or its descendant."}, status=400)
                cursor = cursor.parent
            folder.parent = new_parent
    sibling_qs = Folder.objects.filter(student=student, parent=folder.parent, name=folder.name).exclude(pk=folder.pk)
    if sibling_qs.exists():
        return Response({"detail": "A folder with this name already exists here."}, status=409)
    folder.save()
    return Response(FolderSerializer(folder).data)


@api_view(["DELETE"])
@permission_classes([HasAPIKey & RequiresActiveStudentSubscription()])
@authentication_classes([SessionTokenAuthentication])
def folder_delete(request, folder_id: int):
    student = _get_student_for_user(request.user)
    if not student:
        return Response({"detail": "Student profile not found."}, status=403)
    folder = get_object_or_404(Folder, pk=folder_id, student=student)
    force = request.query_params.get("force") in ("1", "true", "True")
    if not force:
        if folder.children.exists() or folder.snippets.exists() or folder.files.exists():
            return Response({"detail": "Folder is not empty. Pass ?force=1 to delete with contents."}, status=400)
        folder.delete()
        return Response(status=204)

    def _collect_descendants(root):
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
    for f in CodeFile.objects.filter(student=student, folder_id__in=descendant_ids):
        try:
            f.file.delete(save=False)
        except Exception:
            logger.exception("Failed to delete file blob during folder cascade", extra={"file_id": f.id})
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
    student = _get_student_for_user(request.user)
    if not student:
        return Response({"detail": "Student profile not found."}, status=403)
    data = request.data.copy()
    data.pop("student", None)
    snippet_id = data.pop("id", None) or data.pop("pk", None)
    folder_id = data.get("folder")
    folder = None
    if folder_id not in (None, "", 0, "0"):
        try:
            folder = Folder.objects.get(pk=folder_id, student=student)
        except Folder.DoesNotExist:
            return Response({"detail": "Folder not found."}, status=404)
    data["folder"] = folder.id if folder else None

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

    title = (data.get("title") or "").strip()
    language = data.get("language") or ""
    if title and language:
        existing = (
            CodeSnippet.objects.filter(student=student, title__iexact=title, language=language, folder=folder)
            .order_by("-updated_at").first()
        )
        if existing:
            serializer = CodeSnippetSerializer(existing, data=data, partial=True)
            if serializer.is_valid():
                obj = serializer.save()
                return Response(CodeSnippetSerializer(obj).data, status=200)
            return Response(serializer.errors, status=400)

    serializer = CodeSnippetSerializer(data=data)
    if serializer.is_valid():
        obj = serializer.save(student=student)
        return Response(CodeSnippetSerializer(obj).data, status=201)
    return Response(serializer.errors, status=400)


@api_view(["GET"])
@permission_classes([HasAPIKey & RequiresActiveStudentSubscription()])
@authentication_classes([SessionTokenAuthentication])
def snippet_list(request):
    student = _get_student_for_user(request.user)
    if not student:
        return Response({"detail": "Student profile not found."}, status=403)
    qs = CodeSnippet.objects.filter(student=student)
    lesson_id = request.query_params.get("lesson")
    if lesson_id:
        qs = qs.filter(lesson_id=lesson_id)
    folder_id = request.query_params.get("folder")
    if folder_id == "null" or folder_id == "":
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
# CODE FILES (uploads)
# ===========================================================================
MAX_UPLOAD_MB = getattr(settings, "IDE_MAX_UPLOAD_MB", 25)
ALLOWED_EXTENSIONS = getattr(
    settings, "IDE_ALLOWED_EXTENSIONS",
    {"py", "txt", "csv", "json", "xml", "yaml", "yml", "md", "html", "css", "js", "ts", "jsx", "tsx",
     "java", "kt", "c", "cpp", "h", "hpp", "cs", "go", "rs", "php", "sql", "sh", "ipynb",
     "png", "jpg", "jpeg", "gif", "svg", "pdf"}
)

def _ext_ok(name: str) -> bool:
    import os
    ext = os.path.splitext(name)[1].lower().lstrip(".")
    return (not ALLOWED_EXTENSIONS) or (ext in ALLOWED_EXTENSIONS)


@api_view(["POST"])
@permission_classes([HasAPIKey & RequiresActiveStudentSubscription()])
@authentication_classes([SessionTokenAuthentication])
def codefile_upload(request):
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
        student=student, lesson=lesson, folder=folder,
        label=(request.data.get("label") or "").strip()[:255],
        file=up, original_name=up.name[:255],
        content_type=getattr(up, "content_type", "") or "",
        size_bytes=up.size or 0,
    )
    return Response(CodeFileSerializer(obj, context={"request": request}).data, status=201)


@api_view(["GET"])
@permission_classes([HasAPIKey & RequiresActiveStudentSubscription()])
@authentication_classes([SessionTokenAuthentication])
def codefile_list(request):
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
    obj = get_object_or_404(CodeFile, id=file_id)
    student = _get_student_for_user(request.user)
    if request.method == "GET":
        if student and obj.student_id == student.id:
            return Response(CodeFileSerializer(obj, context={"request": request}).data)
        return Response({"detail": "Not allowed."}, status=403)
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
# UPLOAD RESOLVE
# ===========================================================================
@api_view(["GET"])
@permission_classes([HasAPIKey, IsAuthenticated])
@authentication_classes([SessionTokenAuthentication])
def resolve_upload_by_label(request):
    label = request.query_params.get("label", "").strip()
    if not label:
        return Response({"error": "Missing required query param: label"}, status=status.HTTP_400_BAD_REQUEST)
    teacher_profile = getattr(request.user, "teacher_profile", None)
    qs = None
    if teacher_profile:
        qs = CodeFile.objects.filter(
            Q(label__iexact=label) | Q(original_name__iexact=label)
        ).order_by("-created_at")
    else:
        student_profile = getattr(request.user, "student_profile", None)
        if student_profile is None:
            return Response({"error": "No student profile associated with this account"}, status=status.HTTP_403_FORBIDDEN)
        qs = CodeFile.objects.filter(student=student_profile).filter(
            Q(label__iexact=label) | Q(original_name__iexact=label)
        ).order_by("-created_at")

    if teacher_profile is None and qs is None:
        return Response({"error": "No teacher profile associated with this account"}, status=status.HTTP_403_FORBIDDEN)

    file_obj = qs.first()
    if file_obj is None:
        return Response({"error": f'No uploaded file found with label: "{label}"'}, status=status.HTTP_404_NOT_FOUND)

    file_url = request.build_absolute_uri(file_obj.file.url)
    return Response({
        "id": file_obj.id, "label": file_obj.label,
        "original_name": file_obj.original_name, "url": file_url,
        "content_type": file_obj.content_type, "size_bytes": file_obj.size_bytes,
        "lesson": file_obj.lesson_id, "folder": file_obj.folder_id,
    })