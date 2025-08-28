from typing import Any, Dict, List, Optional
import traceback

from django.conf import settings
from django.db.models import Q
from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.response import Response
from rest_framework import status
from rest_framework_api_key.permissions import HasAPIKey

from api.authentication import SessionTokenAuthentication

# Adjust these imports to match your app layout
from orgs.models import OrganizationMembership
from academics.models import StudentProfile
from learning.models import Material, Note, Bookmark, Lesson  # <- Bookmark uses Lesson


def _get_student_for_user(user) -> Optional[StudentProfile]:
    mem = (OrganizationMembership.objects
           .filter(user=user, is_active=True)
           .select_related("organization")
           .order_by("-id")
           .first())
    if mem:
        sp = StudentProfile.objects.filter(user=user, organization=mem.organization).first()
        if sp:
            return sp
    return StudentProfile.objects.filter(user=user).order_by("-id").first()


def _fmt_size(file_obj) -> Optional[str]:
    try:
        if file_obj and hasattr(file_obj, "size") and file_obj.size:
            mb = file_obj.size / (1024.0 * 1024.0)
            return f"{mb:.1f} MB"
    except Exception:
        pass
    return None


@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def my_materials(request):
    """
    My Materials (Material-based)

    Buckets:
      - saved.videos  (Material.kind == "video")
      - saved.audio   (Material.kind == "audio")
      - saved.pdfs    (Material.kind == "pdf")
      - notes         (from Note)  -> force tags: ["Java","Python"]
      - bookmarks     (from Bookmark pointing to Lesson)

    Query params:
      - q: search over Material.title / Material.tags and Note.content/title line / Lesson.name (bookmarks)
      - my_only: '1' => only user's materials (otherwise (owner=user) ∪ (org public))
      - videos_limit, audio_limit, pdfs_limit, notes_limit, bookmarks_limit (ints; default 12)
      - debug=1 -> include traceback on error
    """
    try:
        user = request.user
        student = _get_student_for_user(user)

        def _as_int(v, default):
            try:
                return int(v) if v is not None else default
            except Exception:
                return default

        v_lim = _as_int(request.query_params.get("videos_limit"), 12)
        a_lim = _as_int(request.query_params.get("audio_limit"), 12)
        p_lim = _as_int(request.query_params.get("pdfs_limit"), 12)
        n_lim = _as_int(request.query_params.get("notes_limit"), 12)
        b_lim = _as_int(request.query_params.get("bookmarks_limit"), 12)

        q = (request.query_params.get("q") or "").strip()
        my_only = request.query_params.get("my_only") in {"1", "true", "True"}

        # ---------- Materials ----------
        if my_only or not student:
            m_base = Material.objects.filter(owner=user)
        else:
            m_base = Material.objects.filter(
                Q(owner=user) | Q(organization=student.organization, is_public=True)
            )

        if q:
            # Simple search across title and tags (JSON list)
            m_base = m_base.filter(Q(title__icontains=q) | Q(tags__icontains=q))

        m_base = m_base.select_related("owner").order_by("-updated_at", "-created_at")

        # Videos
        videos_qs = m_base.filter(kind=Material.Kind.VIDEO)[:v_lim]
        videos: List[Dict[str, Any]] = []
        for m in videos_qs:
            owner = getattr(m, "owner", None)
            instructor = (owner.get_full_name() or owner.username) if owner else None
            try:
                video_url = m.file.url if m.file else (m.url or None)
            except ValueError:
                video_url = m.url or None
            videos.append({
                "id": str(m.id),
                "title": m.title,
                "instructor": instructor,
                "duration": "—",         # No duration on Material
                "progress": 0,           # No progress tracking for Material
                "thumbnail": None,       # UI falls back to placeholder
                "videoUrl": video_url,
            })

        # Audio
        audio_qs = m_base.filter(kind=Material.Kind.AUDIO)[:a_lim]
        audio: List[Dict[str, Any]] = []
        for m in audio_qs:
            owner = getattr(m, "owner", None)
            speaker = (owner.get_full_name() or owner.username) if owner else None
            try:
                audio_url = m.file.url if m.file else (m.url or None)
            except ValueError:
                audio_url = m.url or None
            audio.append({
                "id": str(m.id),
                "title": m.title,
                "speaker": speaker,
                "duration": "—",
                "progress": 0,
                "audioUrl": audio_url,
            })

        # PDFs (Downloads)
        pdf_qs = m_base.filter(kind=Material.Kind.PDF)[:p_lim]
        pdfs: List[Dict[str, Any]] = []
        for m in pdf_qs:
            owner = getattr(m, "owner", None)
            author = (owner.get_full_name() or owner.username) if owner else None
            try:
                download_url = m.file.url if m.file else (m.url or None)
            except ValueError:
                download_url = m.url or None
            pdfs.append({
                "id": str(m.id),
                "title": m.title,
                "author": author,
                "pages": None,                      # Not tracked on Material
                "size": _fmt_size(m.file),
                "downloadUrl": download_url,
            })

        # ---------- Notes (force tags ["Java","Python"]) ----------
        notes_out: List[Dict[str, Any]] = []
        if student:
            notes_qs = Note.objects.filter(student=student).order_by("-updated_at")
            if q:
                notes_qs = notes_qs.filter(Q(content__icontains=q))
            for n in notes_qs[:n_lim]:
                content = n.content or ""
                first_line = (content.splitlines()[0] if content else "").strip()
                title = first_line[:80] if first_line else "Note"
                notes_out.append({
                    "id": str(n.id),
                    "title": title,
                    "content": content,
                    "tags": ["Java", "Python"],   # <- forced tags as requested
                    "createdAt": n.created_at.isoformat(),
                    "updatedAt": n.updated_at.isoformat(),
                })

        # ---------- Bookmarks (Bookmark -> Lesson) ----------
        bookmarks_qs = Bookmark.objects.filter(student=student) if student else Bookmark.objects.none()
        if q:
            bookmarks_qs = bookmarks_qs.select_related("lesson").filter(
                Q(lesson__name__icontains=q) | Q(note__icontains=q)
            )
        else:
            bookmarks_qs = bookmarks_qs.select_related("lesson")
        bookmarks_qs = bookmarks_qs.order_by("-created_at")[:b_lim]

        bookmarks_out: List[Dict[str, Any]] = []
        for b in bookmarks_qs:
            lesson: Optional[Lesson] = getattr(b, "lesson", None)
            bookmarks_out.append({
                "id": str(b.id),
                "lessonId": lesson.id if lesson else None,
                "lessonTitle": getattr(lesson, "name", None),
                "positionSeconds": b.position_seconds,
                "note": b.note or "",
                "createdAt": b.created_at.isoformat(),
                "updatedAt": b.updated_at.isoformat(),
            })

        return Response({
            "saved": {"videos": videos, "audio": audio, "pdfs": pdfs},
            "notes": notes_out,
            "bookmarks": bookmarks_out,
        }, status=status.HTTP_200_OK)

    except Exception as e:
        err = {"detail": "Failed to load materials.", "error": f"{type(e).__name__}: {e}"}
        if request.query_params.get("debug") in {"1", "true", "True"} or getattr(settings, "DEBUG", False):
            err["traceback"] = traceback.format_exc()
        return Response(err, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
