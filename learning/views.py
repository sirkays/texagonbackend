from decimal import Decimal
from typing import Any, Dict, List, Optional
import traceback

from django.conf import settings
from django.db.models import Q, Count, Sum
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.response import Response
from rest_framework import status
from rest_framework_api_key.permissions import HasAPIKey

from api.authentication import SessionTokenAuthentication

# ⬇️ Adjust these imports to your app labels if different
from academics.models import StudentProfile
from learning.models import Bookmark, Course, Enrollment, Lesson, Material, Module, Note
from orgs.models import OrganizationMembership
from live.models import LiveSession  # if LiveSession is in a different app, update import


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



def _fmt_duration(seconds: int) -> str:
    seconds = int(seconds or 0)
    h = seconds // 3600
    m = (seconds % 3600) // 60
    return f"{h}h {m}m" if h else f"{m}m"


def _difficulty_from_lessons(n: int) -> str:
    if (n or 0) >= 28:
        return "Advanced"
    if (n or 0) >= 16:
        return "Intermediate"
    return "Beginner"


def _lesson_url(ls: Lesson) -> Optional[str]:
    try:
        return ls.file.url if ls.file else (ls.url or None)
    except Exception:
        return ls.url or None




@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def learning_modules(request):
    """
    LearningModules payload (NO journals).

    Source of truth:
      - videos, audio, pdfs, docs, links: from Lesson ONLY
        (scoped to: lesson.active=True AND module.active=True AND user's enrolled courses)
      - tutorials: from LiveSession ONLY (active=True and within user's enrolled courses)

    Query params:
      - q: search string (applies to lesson name, course, subject)
      - videos_limit, audio_limit, pdfs_limit, docs_limit, links_limit, tutorials_limit
      - debug=1 → include traceback in error response
    """
    try:
        user = request.user
        student = _get_student_for_user(user)

        def _i(v, d):
            try:
                return int(v) if v is not None else d
            except Exception:
                return d

        q = (request.query_params.get("q") or "").strip()
        v_lim = _i(request.query_params.get("videos_limit"), 9)
        a_lim = _i(request.query_params.get("audio_limit"), 9)
        p_lim = _i(request.query_params.get("pdfs_limit"), 12)
        d_lim = _i(request.query_params.get("docs_limit"), 12)
        l_lim = _i(request.query_params.get("links_limit"), 12)
        t_lim = _i(request.query_params.get("tutorials_limit"), 6)

        # Guard: if no student, return empty lists (keeps “active/currently on” contract strict)
        if not student:
            return Response({
                "videos": [], "audio": [], "pdfs": [], "docs": [], "links": [], "tutorials": []
            }, status=status.HTTP_200_OK)

        # ---------- Determine the user's enrolled courses ----------
        enrolled_course_ids = list(
            Enrollment.objects.filter(student=student).values_list("course_id", flat=True)
        )
        if not enrolled_course_ids:
            return Response({
                "videos": [], "audio": [], "pdfs": [], "docs": [], "links": [], "tutorials": []
            }, status=status.HTTP_200_OK)

        # ---------- Build base Lesson queryset ----------
        # Only lessons that are active AND belong to active modules AND those modules belong to the user's enrolled courses.
        base = (
            Lesson.objects.select_related(
                "module", "module__course", "module__course__teacher__user",
                "module__course__subject", "module__course__classroom"
            )
            .filter(
                active=True,
                module__active=True,
                module__course_id__in=enrolled_course_ids,
            )
        )
        if q:
            base = base.filter(
                Q(name__icontains=q) |
                Q(module__course__name__icontains=q) |
                Q(module__course__subject__name__icontains=q)
            )

        # Pre-compute course metadata to avoid N+1
        course_ids = list(
            base.values_list("module__course_id", flat=True).distinct()
        )
        courses = {
            c.id: c for c in
            Course.objects.filter(id__in=course_ids).select_related("teacher__user", "subject")
        }
        # Map: course -> progress for this student
        progress_map = {
            e.course_id: int(e.progress_pct or 0)
            for e in Enrollment.objects.filter(student=student, course_id__in=course_ids)
        }
        # Map: course -> total enrollments (as a proxy for popularity/listeners)
        size_map = {
            r["course_id"]: r["cnt"]
            for r in (Enrollment.objects
                      .filter(course_id__in=course_ids)
                      .values("course_id").annotate(cnt=Count("id")))
        }

        def lesson_item(ls: Lesson) -> Dict[str, Any]:
            c = courses.get(ls.module.course_id)
            teacher_user = getattr(getattr(c, "teacher", None), "user", None) if c else None
            instructor = (teacher_user.get_full_name() or teacher_user.username) if teacher_user else None
            subject_name = getattr(getattr(c, "subject", None), "name", None) if c else None
            return {
                "id": ls.id,
                "title": ls.name,                           # from Lesson
                "content_type": ls.content_type,            # for client-side grouping if needed
                "duration": _fmt_duration(ls.duration_seconds),
                "url": _lesson_url(ls),                     # direct link to file or external url
                "course": getattr(c, "name", None),
                "subject": subject_name,
                "instructor": instructor,
                "module_order": getattr(ls.module, "order", None),
                "lesson_order": ls.order,
                "progress": progress_map.get(getattr(c, "id", None), 0),
                "popularity": size_map.get(getattr(c, "id", None), 0),
                "updated_at": ls.updated_at.isoformat(),
            }

        # ---------- Slice per content type (all from Lesson) ----------
        videos_qs = base.filter(content_type=Lesson.ContentType.VIDEO).order_by("-updated_at", "module__order", "order")
        audio_qs  = base.filter(content_type=Lesson.ContentType.AUDIO).order_by("-updated_at", "module__order", "order")
        pdfs_qs   = base.filter(content_type=Lesson.ContentType.PDF).order_by("-updated_at", "module__order", "order")
        docs_qs   = base.filter(content_type=Lesson.ContentType.DOC).order_by("-updated_at", "module__order", "order")
        links_qs  = base.filter(content_type=Lesson.ContentType.LINK).order_by("-updated_at", "module__order", "order")

        videos = [lesson_item(ls) for ls in videos_qs[:v_lim]]
        audio  = [lesson_item(ls) for ls in audio_qs[:a_lim]]
        pdfs   = [lesson_item(ls) for ls in pdfs_qs[:p_lim]]
        docs   = [lesson_item(ls) for ls in docs_qs[:d_lim]]
        links  = [lesson_item(ls) for ls in links_qs[:l_lim]]

        # ---------- Tutorials from LiveSession (active + enrolled courses) ----------
        tutorials: List[Dict[str, Any]] = []
        now = timezone.now()
        lsessions = (
            LiveSession.objects
            .filter(active=True, course_id__in=enrolled_course_ids)
            .select_related("course", "host__user", "course__subject")
            .order_by("scheduled_at")
        )
        if q:
            lsessions = lsessions.filter(
                Q(title__icontains=q) |
                Q(course__name__icontains=q) |
                Q(host__user__first_name__icontains=q) |
                Q(host__user__last_name__icontains=q)
            )
        for s in lsessions[:t_lim]:
            tutorials.append({
                "id": s.id,
                "title": s.title,
                "type": "Live Session",
                "duration": f"{int(getattr(s, 'duration_minutes', 60) or 60)}m",
                "scheduledAt": s.scheduled_at.isoformat(),
                "course": getattr(s.course, "name", None),
                "subject": getattr(getattr(s.course, "subject", None), "name", None),
                "host": (getattr(s.host.user, "get_full_name", lambda: "")() or s.host.user.username) if getattr(s, "host", None) and getattr(s.host, "user", None) else None,
                "isActiveNow": bool(s.scheduled_at <= now <= s.scheduled_at + timezone.timedelta(minutes=getattr(s, "duration_minutes", 60) or 60)),
            })

        return Response({
            "videos": videos,
            "audio": audio,
            "pdfs": pdfs,
            "docs": docs,
            "links": links,
            "tutorials": tutorials,
        }, status=status.HTTP_200_OK)

    except Exception as e:
        err = {"detail": "Failed to load learning modules.", "error": f"{type(e).__name__}: {e}"}
        if request.query_params.get("debug") in {"1", "true", "True"} or getattr(settings, "DEBUG", False):
            err["traceback"] = traceback.format_exc()
        return Response(err, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


