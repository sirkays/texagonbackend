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
    Returns data for the LearningModules UI (NO journals).

    Keys:
      - videos:      aggregated from Lesson (content_type=VIDEO) per Course
      - audio:       aggregated from Lesson (content_type=AUDIO) per Course
      - pdfs:        flat list from Lesson (content_type=PDF)
      - docs:        flat list from Lesson (content_type=DOC)
      - links:       flat list from Lesson (content_type=LINK)
      - images:      flat list from Lesson (content_type=IMAGE) if your enum has IMAGE
      - tutorials:   from LiveSession (rendered under the “Live Session” tab)

    Query params:
      - q: search string
      - videos_limit, audio_limit, pdfs_limit, docs_limit, links_limit, images_limit, tutorials_limit, reco_limit (if you extend)
      - debug=1 to include traceback on error
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
        i_lim = _i(request.query_params.get("images_limit"), 12)
        t_lim = _i(request.query_params.get("tutorials_limit"), 6)

        # ---------- Base Lessons ----------
        base = Lesson.objects.select_related(
            "module", "module__course", "module__course__teacher__user",
            "module__course__subject", "module__course__classroom"
        )
        if student:
            base = base.filter(module__course__organization=student.organization)
        if q:
            base = base.filter(
                Q(name__icontains=q) |
                Q(module__course__name__icontains=q) |
                Q(module__course__subject__name__icontains=q)
            )

        # ---------- VIDEOS (aggregate by Course) ----------
        videos: List[Dict[str, Any]] = []
        video_agg = (
            base.filter(content_type=Lesson.ContentType.VIDEO)
                .values("module__course_id")
                .annotate(lessons_cnt=Count("id"), total_sec=Sum("duration_seconds"))
        )
        course_ids_v = [row["module__course_id"] for row in video_agg]
        courses_v = {
            c.id: c for c in
            Course.objects.filter(id__in=course_ids_v).select_related("teacher__user", "subject", "classroom")
        }
        progress_map_v = {}
        students_map_v = {}
        if student and course_ids_v:
            enr_qs = Enrollment.objects.filter(course_id__in=course_ids_v, student=student)
            progress_map_v = {e.course_id: int(e.progress_pct or 0) for e in enr_qs}
            sz_qs = (Enrollment.objects.filter(course_id__in=course_ids_v)
                                   .values("course_id").annotate(cnt=Count("id")))
            students_map_v = {r["course_id"]: r["cnt"] for r in sz_qs}

        for row in sorted(video_agg, key=lambda x: (x["total_sec"] or 0), reverse=True)[:v_lim]:
            cid = row["module__course_id"]
            c = courses_v.get(cid)
            if not c:
                continue
            teacher_user = getattr(getattr(c, "teacher", None), "user", None)
            instructor = (teacher_user.get_full_name() or teacher_user.username) if teacher_user else "Instructor"
            lessons_cnt = int(row["lessons_cnt"] or 0)
            duration_label = _fmt_duration(int(row["total_sec"] or 0))
            progress = progress_map_v.get(cid, 0)
            videos.append({
                "title": c.name,
                "instructor": instructor,
                "duration": duration_label or "—",
                "lessons": lessons_cnt,
                "progress": progress,
                "rating": 4.7,  # dummy
                "students": students_map_v.get(cid, 0),
                "level": _difficulty_from_lessons(lessons_cnt),
                "completed": progress >= 100,
            })

        if not videos:
            # Safe dummy fallback
            videos = [
                {"title": "React Hooks Masterclass","instructor": "Sarah Wilson","duration": "4h 30m","lessons": 24,"progress": 65,"rating": 4.8,"students": 12500,"level": "Intermediate","completed": False},
                {"title": "Python for Beginners","instructor": "John Martinez","duration": "6h 15m","lessons": 32,"progress": 100,"rating": 4.9,"students": 25600,"level": "Beginner","completed": True},
                {"title": "Advanced JavaScript Concepts","instructor": "Emily Chen","duration": "5h 45m","lessons": 28,"progress": 30,"rating": 4.7,"students": 8900,"level": "Advanced","completed": False},
            ][:v_lim]

        # ---------- AUDIO (aggregate by Course) ----------
        audio: List[Dict[str, Any]] = []
        audio_agg = (
            base.filter(content_type=Lesson.ContentType.AUDIO)
                .values("module__course_id")
                .annotate(episodes=Count("id"), total_sec=Sum("duration_seconds"))
        )
        course_ids_a = [row["module__course_id"] for row in audio_agg]
        courses_a = {
            c.id: c for c in
            Course.objects.filter(id__in=course_ids_a).select_related("teacher__user", "subject")
        }
        listeners_map = {}
        a_progress_map = {}
        if student and course_ids_a:
            a_enr_qs = Enrollment.objects.filter(course_id__in=course_ids_a, student=student)
            a_progress_map = {e.course_id: int(e.progress_pct or 0) for e in a_enr_qs}
            a_sz_qs = (Enrollment.objects.filter(course_id__in=course_ids_a)
                                     .values("course_id").annotate(cnt=Count("id")))
            listeners_map = {r["course_id"]: r["cnt"] for r in a_sz_qs}

        for row in sorted(audio_agg, key=lambda x: (x["total_sec"] or 0), reverse=True)[:a_lim]:
            cid = row["module__course_id"]
            c = courses_a.get(cid)
            if not c:
                continue
            host_user = getattr(getattr(c, "teacher", None), "user", None)
            host = (host_user.get_full_name() or host_user.username) if host_user else "Host"
            audio.append({
                "title": c.name,
                "host": host,
                "episodes": int(row["episodes"] or 0),
                "duration": _fmt_duration(int(row["total_sec"] or 0)) or "—",
                "progress": a_progress_map.get(cid, 0),
                "rating": 4.6,  # dummy
                "listeners": listeners_map.get(cid, 0),
                "category": getattr(getattr(c, "subject", None), "name", "General"),
            })

        if not audio:
            audio = [
                {"title": "Tech Career Podcast Series","host": "Industry Experts","episodes": 15,"duration": "12h total","progress": 40,"rating": 4.6,"listeners": 5600,"category": "Career Development"},
                {"title": "JavaScript Deep Dive Audio Course","host": "Dev Academy","episodes": 20,"duration": "8h 30m","progress": 75,"rating": 4.8,"listeners": 3400,"category": "Programming"},
            ][:a_lim]

        # ---------- Flat lists by Lesson type ----------
        def _flat_from_lessons(qs, limit: int) -> List[Dict[str, Any]]:
            items: List[Dict[str, Any]] = []
            for ls in qs[:limit]:
                course = getattr(ls.module, "course", None)
                subject = getattr(course, "subject", None)
                items.append({
                    "title": ls.name,
                    "course": getattr(course, "name", None),
                    "subject": getattr(subject, "name", None),
                    "duration": _fmt_duration(int(ls.duration_seconds or 0)) if getattr(ls, "duration_seconds", None) is not None else "—",
                    "url": _lesson_url(ls),
                    "module_order": getattr(ls.module, "order", None),
                    "lesson_order": ls.order,
                    "updated_at": ls.updated_at.isoformat(),
                })
            return items

        pdfs_qs = base.filter(content_type=Lesson.ContentType.PDF).order_by("-updated_at")
        pdfs = _flat_from_lessons(pdfs_qs, p_lim)

        docs_qs = base.filter(content_type=Lesson.ContentType.DOC).order_by("-updated_at")
        docs = _flat_from_lessons(docs_qs, d_lim)

        links_qs = base.filter(content_type=Lesson.ContentType.LINK).order_by("-updated_at")
        links = _flat_from_lessons(links_qs, l_lim)

        # IMAGE is optional; include if your enum defines it
        images: List[Dict[str, Any]] = []
        image_ct = getattr(Lesson.ContentType, "IMAGE", None)
        if image_ct:
            images_qs = base.filter(content_type=image_ct).order_by("-updated_at")
            images = _flat_from_lessons(images_qs, i_lim)

        # ---------- Tutorials from LiveSession (Live Session tab) ----------
        tutorials: List[Dict[str, Any]] = []
        if student:
            now = timezone.now()
            course_ids_for_sessions = list(
                Enrollment.objects.filter(student=student).values_list("course_id", flat=True)
            )
            if course_ids_for_sessions:
                ls_qs = (LiveSession.objects
                         .filter(course_id__in=course_ids_for_sessions)
                         .select_related("course", "host__user", "course__subject")
                         .order_by("scheduled_at"))
                if q:
                    ls_qs = ls_qs.filter(
                        Q(title__icontains=q) |
                        Q(course__name__icontains=q) |
                        Q(host__user__first_name__icontains=q) |
                        Q(host__user__last_name__icontains=q)
                    )
                for ls in ls_qs[:t_lim]:
                    is_active = (ls.scheduled_at <= now <= ls.scheduled_at + timezone.timedelta(minutes=ls.duration_minutes))
                    subj = getattr(getattr(ls.course, "subject", None), "name", "General")
                    tutorials.append({
                        "title": ls.title,
                        "type": "Live Session",
                        "steps": 8,  # dummy
                        "duration": f"{int(ls.duration_minutes)}m",
                        "difficulty": "Intermediate" if ls.duration_minutes >= 60 else "Beginner",
                        "technologies": [subj],
                        "sessionCategory": "Private",
                        "isActive": bool(is_active),
                        "scheduledAt": ls.scheduled_at.isoformat(),
                    })

        if not tutorials:
            tutorials = [
                {"title": "Build a Full-Stack E-commerce App","type": "Project Tutorial","steps": 12,"duration": "8h","difficulty": "Advanced","technologies": ["React", "Node.js", "MongoDB"],"sessionCategory": "Private","isActive": True},
                {"title": "Create a REST API with Express","type": "Step-by-step Guide","steps": 8,"duration": "3h","difficulty": "Intermediate","technologies": ["Node.js", "Express", "PostgreSQL"],"sessionCategory": "General","isActive": False},
            ][:t_lim]

        return Response({
            "videos": videos,
            "audio": audio,
            "pdfs": pdfs,
            "docs": docs,
            "links": links,
            "images": images,      # empty if your enum lacks IMAGE
            "tutorials": tutorials # LiveSession-backed
        }, status=status.HTTP_200_OK)

    except Exception as e:
        err = {"detail": "Failed to load learning modules.", "error": f"{type(e).__name__}: {e}"}
        if request.query_params.get("debug") in {"1", "true", "True"} or getattr(settings, "DEBUG", False):
            err["traceback"] = traceback.format_exc()
        return Response(err, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
