from decimal import Decimal
from typing import Any, Dict, List, Optional
import traceback
import os
import re
import json
import uuid
import logging
import time
import boto3
import cloudinary
import cloudinary.utils

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q, Count, Sum, Prefetch
from django.db.utils import DataError
from django.utils import timezone
from django.http import JsonResponse

from rest_framework import status
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.decorators import (
    api_view,
    permission_classes,
    authentication_classes,
    parser_classes,
)

from rest_framework_api_key.permissions import HasAPIKey

from api.authentication import SessionTokenAuthentication
from api.permissions import RequiresActiveStudentSubscription
from api.serializers import LessonSerializer

from academics.models import StudentProfile, TeacherProfile
from learning.models import (
    Bookmark,
    Course,
    Enrollment,
    Lesson,
    Material,
    Module,
    Note,
    ModuleCategory,
)
from orgs.models import OrganizationMembership
from live.models import LiveSession
from .serializers import CourseGeneralActivationSerializer


logger = logging.getLogger(__name__)

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
@permission_classes([HasAPIKey & RequiresActiveStudentSubscription(allow_page=True)])
@authentication_classes([SessionTokenAuthentication])
def my_materials(request):
    """
    Query params:
      - q: search term
      - my_only: '1' => only user's materials
      - videos_limit, audio_limit, pdfs_limit, notes_limit, bookmarks_limit (ints; default 12)
    """
    try:
        user = request.user
        student = _get_student_for_user(user)

        # get_course_allowed accesses request.session which may not exist for token-auth.
        # Wrap in try/except so token-auth requests still work.
        enrolled_course_ids = None
        returned_count = 0
        try:
            data, returned_count = student.get_course_allowed(request, is_session=True)
            if returned_count == 2:
                enrolled_course_ids = data[0]
        except Exception:
            pass  # Token-auth: skip session-based restriction; rely on enrollment check below

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
        allowed_statuses = [Enrollment.Status.ACTIVE, Enrollment.Status.COMPLETED]

        # 1) Always start from materials owned by this user.
        # Do NOT use chained .filter().filter() on the same reverse-FK (causes cross-join / SQLite * error).
        m_base = Material.objects.filter(owner=user)

        if not my_only and student:
            # Use a separate subquery for enrolled course IDs — avoids the multiplication error.
            enrolled_ids = list(
                Enrollment.objects.filter(
                    student=student,
                    status__in=allowed_statuses,
                ).values_list("course_id", flat=True)
            )
            if enrolled_ids:
                m_base = m_base.filter(lesson__module__course__pk__in=enrolled_ids)

        # 2) Do not filter the queryset by enrolled_course_ids anymore, so they show up as locked rather than hidden.
        # But we still keep enrolled_course_ids to compute 'blur' on each item.
        pass

        # 3) Search
        if q:
            m_base = m_base.filter(
                Q(title__icontains=q) |
                Q(tags__icontains=q)
            )

        m_base = m_base.select_related("owner", "lesson__module__course").order_by("-updated_at", "-created_at")


        def _owner_name(m):
            owner = m.lesson.module.course.teacher.user
            return owner.get_full_name() if owner else None

        def _safe_url(file_field, fallback_url):
            try:
                url = file_field.url if file_field else (fallback_url or None)
                if url and url.startswith('/'):
                    return request.build_absolute_uri(url)
                return url
            except Exception:
                url = fallback_url or None
                if url and url.startswith('/'):
                    return request.build_absolute_uri(url)
                return url

        # Videos
        videos = []
        for m in m_base.filter(kind=Material.Kind.VIDEO)[:v_lim]:
            course_id = m.lesson.module.course_id
            blur = (enrolled_course_ids is not None) and (course_id not in enrolled_course_ids)
            if blur:
                file = ""
            else:
                if m.file and getattr(settings, "IS_LOCAL", False) and getattr(m, 'lesson', None):
                    file = request.build_absolute_uri(f"/learning/api/stream-video/{m.lesson.id}/")
                else:
                    file = _safe_url(getattr(m, "file", None), getattr(m, "url", None)) if m.file or m.url else None
            
            videos.append({
                "id": str(m.id),
                "title": m.title,
                "instructor": _owner_name(m),
                "duration": "—",
                "progress": 0,
                "thumbnail": _safe_url(getattr(m, "cover_image", None), None),
                "videoUrl":file,
                "blur":blur,
                "lesson_id":m.lesson.id
            })

        # Audio
        audio = []
        for m in m_base.filter(kind=Material.Kind.AUDIO)[:a_lim]:
            course_id = m.lesson.module.course_id
            blur = (enrolled_course_ids is not None) and (course_id not in enrolled_course_ids)
            if blur:
                file = ""
            else:
                if m.file and getattr(settings, "IS_LOCAL", False) and getattr(m, 'lesson', None):
                    file = request.build_absolute_uri(f"/learning/api/stream-video/{m.lesson.id}/")
                else:
                    file = _safe_url(getattr(m, "file", None), getattr(m, "url", None)) if m.file or m.url else None
            
            audio.append({
                "id": str(m.id),
                "title": m.title,
                "speaker": _owner_name(m),
                "duration": "—",
                "progress": 0,
                "audioUrl": file,
                "blur":blur,
                "lesson_id":m.lesson.id
            })

        # PDFs
        pdfs = []
        for m in m_base.filter(kind=Material.Kind.PDF)[:p_lim]:
            course_id = m.lesson.module.course_id
            blur = (enrolled_course_ids is not None) and (course_id not in enrolled_course_ids)
            if blur:
                file = ""
            else:
                file =  _safe_url(getattr(m, "file", None), getattr(m, "url", None)) if m.file or m.url else None
            
            pdfs.append({
                "id": str(m.id),
                "title": m.title,
                "author": _owner_name(m),
                "pages": None,
                "size": _fmt_size(m.file),
                "downloadUrl": file,
                "blur":blur,
                "lesson_id":m.lesson.id
            })

        # ---------- Notes ----------
        notes_out = []
        if student:
            notes_qs = Note.objects.filter(student=student).order_by("-updated_at")
            if q:
                notes_qs = notes_qs.filter(
                    Q(title__icontains=q) | Q(content__icontains=q)
                )

            for n in notes_qs[:n_lim]:
                notes_out.append({
                    "id": str(n.id),
                    "title": n.title or "",
                    "content": n.content or "",
                    "tags": ["Java", "Python"],  # forced
                    "created_at": n.created_at.isoformat(),
                    "updated_at": n.updated_at.isoformat(),
                })

        # ---------- Bookmarks ----------
        bookmarks_qs = Bookmark.objects.filter(student=student) if student else Bookmark.objects.none()
        bookmarks_qs = bookmarks_qs.select_related("lesson")

        if q:
            bookmarks_qs = bookmarks_qs.filter(
                Q(lesson__name__icontains=q) | Q(note__icontains=q)
            )

        bookmarks_qs = bookmarks_qs.order_by("-created_at")[:b_lim]

        bookmarks_out = []
        for b in bookmarks_qs:
            lesson = getattr(b, "lesson", None)
            bookmarks_out.append({
                "id": str(b.id),
                "lessonId": lesson.id if lesson else None,
                "lessonTitle": getattr(lesson, "name", None),
                "position_seconds": b.position_seconds,
                "note": b.note or "",
                "created_at": b.created_at.isoformat(),
                "updated_at": b.updated_at.isoformat(),
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



def _coerce_int_list(v) -> List[int]:
    if v is None:
        return []
    if isinstance(v, (list, tuple, set)):
        out = []
        for x in v:
            try:
                out.append(int(x))
            except Exception:
                pass
        return out
    try:
        return [int(v)]
    except Exception:
        return []

def _read_json_body_for_get(request) -> Dict[str, Any]:
    """Allows JSON body on GET (non-standard, but supported here on purpose)."""
    if request.method != "GET":
        return {}
    ctype = (request.META.get("CONTENT_TYPE") or request.META.get("HTTP_CONTENT_TYPE") or "").split(";")[0].strip().lower()
    if ctype != "application/json":
        return {}
    try:
        raw = (request.body or b"").decode("utf-8").strip()
        if not raw:
            return {}
        return json.loads(raw)
    except Exception:
        return {}


@api_view(["GET"])
@permission_classes([HasAPIKey & RequiresActiveStudentSubscription(allow_page=True)])
@authentication_classes([SessionTokenAuthentication])
def learning_modules(request):
    """
    LearningModules payload (NO journals).

    Accepts module filter in either:
      - query params:  ?module_id=12   OR  ?module_id=1&module_id=2
      - JSON body (GET): {"module_id": 12} OR {"module_ids": [1,2]}

    Data sources:
      - videos/audio/pdfs/docs/links: Lesson (active=True, module.active=True, enrolled courses)
      - tutorials: LiveSession (active=True, in user's enrolled courses; filtered by modules' courses if provided)
    """

    import traceback
    

    try:
        user = request.user
        student = _get_student_for_user(user)
        data, returned_count = student.get_course_allowed(request, is_session=True)

        allowed_course_ids = None
        if returned_count == 2:
            allowed_course_ids = data[0]

        enrolled_course_ids = None


        # -------- parse limits/search --------
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

        # -------- module filter (query OR JSON body on GET) --------
        body = _read_json_body_for_get(request)
        module_ids = []  # collect from query first …
        qp_multi = request.query_params.getlist("module_id") or request.query_params.getlist("module")
        if qp_multi:
            module_ids.extend(_coerce_int_list(qp_multi))
        else:
            qp_single = request.query_params.get("module_id") or request.query_params.get("module")
            module_ids.extend(_coerce_int_list(qp_single))

        # … then from JSON body (supports module_id / module / module_ids)
        for key in ("module_id", "module", "module_ids"):
            if key in body:
                module_ids.extend(_coerce_int_list(body[key]))

        module_ids = list({mid for mid in module_ids if isinstance(mid, int)})

        # -------- guard: need a student + enrollments --------
        if not student:
            return Response({"videos": [], "audio": [], "pdfs": [], "docs": [], "links": [], "tutorials": []},
                            status=status.HTTP_200_OK)

        if enrolled_course_ids is None:
            enrolled_course_ids = list(
                Enrollment.objects.filter(student=student, status__in=[Enrollment.Status.ACTIVE, Enrollment.Status.COMPLETED]).values_list("course_id", flat=True)
            )
        
        if not enrolled_course_ids:
            return Response({"videos": [], "audio": [], "pdfs": [], "docs": [], "links": [], "tutorials": []},
                            status=status.HTTP_200_OK)


        # -------- base Lesson queryset --------
        base = (
            Lesson.objects.select_related(
                "module", "module__course", "module__course__teacher__user",
                "module__course__subject", "module__course__classroom"
            )
            .filter(active=True, module__active=True, module__course_id__in=enrolled_course_ids)
        )

        if module_ids:
            base = base.filter(module_id__in=module_ids)
        if q:
            base = base.filter(
                Q(name__icontains=q) |
                Q(module__course__name__icontains=q) |
                Q(module__course__subject__name__icontains=q)
            )

        # Preload course metadata
        course_ids = list(base.values_list("module__course_id", flat=True).distinct())
        courses = {
            c.id: c for c in Course.objects.filter(id__in=course_ids).select_related("teacher__user", "subject")
        }
        progress_map = {
            e.course_id: int(e.progress_pct or 0)
            for e in Enrollment.objects.filter(student=student,  status__in=[Enrollment.Status.ACTIVE, Enrollment.Status.COMPLETED], course_id__in=course_ids)
        }
        size_map = {
            r["course_id"]: r["cnt"]
            for r in (Enrollment.objects.filter(course_id__in=course_ids,  status__in=[Enrollment.Status.ACTIVE, Enrollment.Status.COMPLETED])
                      .values("course_id").annotate(cnt=Count("id")))
        }

        def lesson_item(ls: Lesson) -> Dict[str, Any]:
            c = courses.get(ls.module.course_id)
            teacher_user = getattr(getattr(c, "teacher", None), "user", None) if c else None
            instructor = (teacher_user.get_full_name() or teacher_user.username) if teacher_user else None
            subject_name = getattr(getattr(c, "subject", None), "name", None) if c else None
            saved_lesson_ids = set(
                Material.objects.filter(owner=user, active=True, lesson__isnull=False)
                .values_list("lesson_id", flat=True)
            )
            blur = (returned_count == 2) and (allowed_course_ids is not None) and (c.id not in allowed_course_ids) if c else False
            if blur:
                file = ""
            else:
                try:
                    file = request.build_absolute_uri(ls.file.url) if ls.file else None
                except ValueError:
                    file = None
            
            lesson_url_val = file if blur else _lesson_url(ls)
            if lesson_url_val and lesson_url_val.startswith('/'):
                lesson_url_val = request.build_absolute_uri(lesson_url_val)
                
            try:
                cover_image_url = request.build_absolute_uri(ls.cover_image.url) if ls.cover_image else None
            except ValueError:
                cover_image_url = None
            
            return {
                "id": ls.id,
                "title": ls.name,
                "content_type": ls.content_type,
                "duration": _fmt_duration(ls.duration_seconds),
                "url": lesson_url_val,
                "file": file,
                "cover_image": cover_image_url,
                "course_id": c.id if c else None,
                "course": getattr(c, "name", None),
                "subject": subject_name,
                "instructor": instructor,
                "module_id": ls.module_id,
                "module_order": getattr(ls.module, "order", None),
                "lesson_order": ls.order,
                "progress": progress_map.get(getattr(c, "id", None), 0),
                "popularity": size_map.get(getattr(c, "id", None), 0),
                "updated_at": ls.updated_at.isoformat(),
                "is_saved": ls.id in saved_lesson_ids,
                "blur":blur
            }
        # -------- per content-type lists --------
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
        # -------- tutorials (LiveSession) --------
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
            dur = int(getattr(s, "duration_minutes", 60) or 60)
            tutorials.append({
                "id": s.id,
                "title": s.title,
                "type": "Live Session",
                "duration": f"{dur}m",
                "scheduledAt": s.scheduled_at.isoformat(),
                "course": getattr(s.course, "name", None),
                "subject": getattr(getattr(s.course, "subject", None), "name", None),
                "host": (s.host.user.get_full_name() or s.host.user.username) if getattr(s, "host", None) and getattr(s.host, "user", None) else None,
                "isActiveNow": bool(s.scheduled_at <= now <= s.scheduled_at + timezone.timedelta(minutes=dur)),
            })
        return Response({
            "videos": videos,
            "audio": audio,
            "pdfs": pdfs,
            "docs": docs,
            "links": links,
            "tutorials": tutorials,
            "filters": {"module_ids": module_ids},  # echo back for the client
        }, status=status.HTTP_200_OK)

    except Exception as e:
        print(e)
        err = {"detail": "Failed to load learning modules.", "error": f"{type(e).__name__}: {e}"}
        if request.query_params.get("debug") in {"1", "true", "True"} or getattr(settings, "DEBUG", False):
            import traceback
            err["traceback"] = traceback.format_exc()
        return Response(err, status=status.HTTP_500_INTERNAL_SERVER_ERROR)




def _kind_from_lesson(lesson: Lesson) -> str:
    """Map Lesson.content_type to Material.kind."""
    m = {
        Lesson.ContentType.VIDEO: Material.Kind.VIDEO,
        Lesson.ContentType.AUDIO: Material.Kind.AUDIO,
        Lesson.ContentType.PDF:   Material.Kind.PDF,
        Lesson.ContentType.DOC:   Material.Kind.DOC,
        Lesson.ContentType.LINK:  Material.Kind.OTHER,  # no LINK kind on Material
    }
    return m.get(lesson.content_type, Material.Kind.OTHER)




def _file_url_or_none(request, file_field):
    if not file_field:   # handles None/""/no file
        return None
    try:
        return _abs_url_or_none(request, file_field.url)
    except ValueError:
        # covers "has no file associated"
        return None

def _material_to_dict(request, m: Material, lesson: Lesson) -> Dict[str, Any]:
    return {
        "id": m.id,
        "title": m.title,
        "kind": m.kind,
        "tags": m.tags or [],
        "is_public": m.is_public,
        "active": m.active,
        "organization": {
            "id": m.organization_id,
            "name": getattr(m.organization, "name", None),
        },
        "file_url": _file_url_or_none(request, m.file),
        "url": m.url or None,
        "created_at": m.created_at.isoformat(),
        "lesson": {
            "id": lesson.id,
            "title": lesson.name,
        } if lesson else None,
    }

@api_view(["GET"])
def stream_local_video(request, lesson_id: int):
    import os, re
    from django.http import StreamingHttpResponse, HttpResponse
    from django.conf import settings
    from .models import Lesson

    lesson = Lesson.objects.filter(id=lesson_id).first()
    if not lesson:
        return HttpResponse(status=404)
        
    path = None
    if lesson.file:
        try:
            path = lesson.file.path
        except ValueError:
            pass
    
    if not path:
        value = (lesson.url or "").strip()
        if value and not (value.startswith("http://") or value.startswith("https://")):
            path = os.path.join(settings.MEDIA_ROOT, value.replace('/', os.sep))
            
    if not path or not os.path.exists(path):
        return HttpResponse(status=404)

    size = os.path.getsize(path)
    range_header = request.META.get('HTTP_RANGE', '').strip()
    range_match = re.match(r'bytes=(?P<start>\d+)-(?P<end>\d*)', range_header) if range_header else None

    # Detect content type by file extension
    lower_path = path.lower()
    if lower_path.endswith('.pdf'):
        content_type = 'application/pdf'
    elif lower_path.endswith('.mp3') or lower_path.endswith('.m4a'):
        content_type = 'audio/mpeg'
    elif lower_path.endswith('.wav'):
        content_type = 'audio/wav'
    elif lower_path.endswith('.aac'):
        content_type = 'audio/aac'
    elif lower_path.endswith('.ogg'):
        content_type = 'audio/ogg'
    elif lower_path.endswith('.webm'):
        content_type = 'video/webm'
    elif lower_path.endswith('.mov'):
        content_type = 'video/quicktime'
    elif lower_path.endswith('.avi'):
        content_type = 'video/x-msvideo'
    else:
        content_type = 'video/mp4'

    if range_match:
        start = int(range_match.group('start'))
        end = range_match.group('end')
        end = int(end) if end else size - 1
        length = end - start + 1
        
        def file_iterator():
            with open(path, 'rb') as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk_size = min(8192, remaining)
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    yield chunk
                    remaining -= len(chunk)
                    
        response = StreamingHttpResponse(file_iterator(), status=206, content_type=content_type)
        response['Content-Range'] = f'bytes {start}-{end}/{size}'
        response['Accept-Ranges'] = 'bytes'
        response['Content-Length'] = str(length)
    else:
        def file_iterator():
            with open(path, 'rb') as f:
                chunk = f.read(8192)
                while chunk:
                    yield chunk
                    chunk = f.read(8192)
        response = StreamingHttpResponse(file_iterator(), content_type=content_type)
        response['Accept-Ranges'] = 'bytes'
        response['Content-Length'] = str(size)

    # For PDFs: instruct browser to render inline (not download)
    if content_type == 'application/pdf':
        filename = os.path.basename(path)
        response['Content-Disposition'] = f'inline; filename="{filename}"'

    return response



@api_view(["POST"])
@permission_classes([HasAPIKey & RequiresActiveStudentSubscription()])
@authentication_classes([SessionTokenAuthentication])
def save_lesson_to_my_materials(request, lesson_id: int):
    """
    Save a Lesson as a Material for the authenticated user (idempotent).

    Path:  POST /api/materials/save-lesson/<lesson_id>/
    Body (optional):
      - title: str (override Material.title)
      - tags: list[str] (extra tags to store)
      - is_public: bool (default False)

    Rules:
      - Uses Lesson.course.organization as Material.organization
      - kind is derived from Lesson.content_type
      - Idempotent by (owner, organization, lesson)
      - Blocks cross-organization save if student's org != lesson's org (403)
    """
    try:
        user = request.user
        student = _get_student_for_user(user)

        # Fetch lesson (must be active)
        try:
            lesson = (
                Lesson.objects
                .select_related("module", "module__course", "module__course__organization")
                .get(pk=lesson_id, active=True)
            )
        except Lesson.DoesNotExist:
            return Response({"detail": "Lesson not found or inactive."}, status=status.HTTP_404_NOT_FOUND)

        course: Course = lesson.module.course
        org: Organization = course.organization

        # Optional org-guard: only allow saving lessons from user's org
        if student and getattr(student, "organization_id", None) != org.id:
            return Response(
                {"detail": "You cannot save materials from another organization."},
                status=status.HTTP_403_FORBIDDEN
            )

        # Enforce subscription course general activation check if subscription has expired
        if student:
            data, returned_count = student.get_course_allowed(request, is_session=True)
            if returned_count == 2:
                if course.id not in data[0]:
                    return Response({"detail": "Course access has expired. Please renew your subscription."}, status=status.HTTP_403_FORBIDDEN)

        # Determine kind + sources
        kind = _kind_from_lesson(lesson)
        src_url = (lesson.url or "").strip() or None
        has_file = bool(getattr(lesson, "file", None) and lesson.file)

        # ✅ Idempotent lookup by lesson FK
        existing = (
            Material.objects
            .filter(owner=user, organization=org, lesson=lesson)
            .first()
        )

        # Prepare fields
        title = (request.data.get("title") or lesson.name or "").strip() or lesson.name
        is_public = bool(request.data.get("is_public") or False)

        # Tags
        input_tags = request.data.get("tags") or []
        try:
            input_tags = [str(t) for t in input_tags] if isinstance(input_tags, (list, tuple)) else []
        except Exception:
            input_tags = []

        base_tags = [lesson.content_type, f"lesson:{lesson.id}"]
        tags = list(dict.fromkeys(base_tags + input_tags))  # unique, preserve order
        if existing:
            updated_fields = []

            # keep lesson link correct
            if existing.lesson_id != lesson.id:
                existing.lesson = lesson
                updated_fields.append("lesson")

            # keep kind in sync with lesson content_type
            if existing.kind != kind:
                existing.kind = kind
                updated_fields.append("kind")

            if existing.title != title:
                existing.title = title
                updated_fields.append("title")

            if existing.tags != tags:
                existing.tags = tags
                updated_fields.append("tags")

            if existing.is_public != is_public:
                existing.is_public = is_public
                updated_fields.append("is_public")

            if not existing.active:
                existing.active = True
                updated_fields.append("active")

            # Sync sources (url/file) to match lesson
            desired_url = src_url or ""
            if (existing.url or "") != desired_url:
                existing.url = desired_url
                updated_fields.append("url")

            if has_file and existing.file != lesson.file:
                existing.file = lesson.file
                updated_fields.append("file")

            if lesson.cover_image and existing.cover_image != lesson.cover_image:
                existing.cover_image = lesson.cover_image
                updated_fields.append("cover_image")

            if updated_fields:
                existing.save(update_fields=updated_fields)
            return Response(
                {"detail": "already_saved", "material": _material_to_dict(request, existing, lesson)},
                status=status.HTTP_200_OK
            )

        # Create new Material
        m = Material(
            owner=user,
            organization=org,
            title=title,
            kind=kind,
            is_public=is_public,
            active=True,
            url=src_url or "",
            tags=tags,
            lesson=lesson,  # ✅ link it
        )

        if lesson.cover_image:
            m.cover_image = lesson.cover_image

        if has_file:
            m.file = lesson.file  # reference the same stored file

        m.save()

        return Response(
            {"detail": "saved", "material": _material_to_dict(request, m, lesson)},
            status=status.HTTP_201_CREATED
        )

    except Exception as e:
        print(e)
        err = {"detail": "Failed to save lesson to My Materials.", "error": f"{type(e).__name__}: {e}"}
        if request.query_params.get("debug") in {"1", "true", "True"} or getattr(settings, "DEBUG", False):
            err["traceback"] = traceback.format_exc()
        return Response(err, status=status.HTTP_500_INTERNAL_SERVER_ERROR)



def _safe_file_or_url(request, ls: Lesson) -> Optional[str]:
    """Prefer file.url if present, else fall back to ls.url."""
    from django.conf import settings
    try:
        if ls.file:
            if getattr(settings, "IS_LOCAL", False) and getattr(ls, 'content_type', '') in ('video', 'audio'):
                return request.build_absolute_uri(f"/learning/api/stream-video/{ls.id}/")
            return request.build_absolute_uri(ls.file.url)
    except Exception:
        pass
    url = ls.url or None
    if url and url.startswith('/'):
        return request.build_absolute_uri(url)
    return url


def _int(v, default=None, cap=None) -> Optional[int]:
    try:
        x = int(v) if v is not None else default
        return min(x, cap) if (cap is not None and x is not None) else x
    except Exception:
        return default


@api_view(["GET"])
@permission_classes([HasAPIKey & RequiresActiveStudentSubscription(allow_page=True)])
@authentication_classes([SessionTokenAuthentication])
def resource_materials(request):
    """
    ResourceMaterials payload (PDFs, Videos, Audio, Journals) driven by Lesson.

    Scoping / defaults:
      - Categories = user's ACTIVE courses (Enrollment.status=ACTIVE & Course.is_active=True)
      - Default course = first active course; default module = that course's first active module
      - You may override with ?course_id=<id>&module_id=<id>
      - Only active Lessons are returned (Lesson.active=True and Module.active=True)

    Query params:
      - q: search text (applies to Lesson.name, Course.name, Subject.name)
      - course_id: restrict to a specific course
      - module_id: restrict to a specific module of the selected course
      - pdfs_limit, videos_limit, audio_limit, journals_limit (default 9 each)
      - debug=1 → include traceback
    """
    try:
        user = request.user
        student = _get_student_for_user(user)

        data, returned_count = student.get_course_allowed(request, is_session=True)
        print(data, "ddd")
        allowed_course_ids = None
        if returned_count == 2:
            allowed_course_ids = data[0]

        course_ids = None

        q = (request.query_params.get("q") or "").strip()
        course_id = _int(request.query_params.get("course_id"))
        module_id = _int(request.query_params.get("module_id"))

        pdfs_limit = _int(request.query_params.get("pdfs_limit"), 9, cap=50)
        vids_limit = _int(request.query_params.get("videos_limit"), 9, cap=50)
        auds_limit = _int(request.query_params.get("audio_limit"), 9, cap=50)
        journ_limit = _int(request.query_params.get("journals_limit"), 9, cap=50)

        # --- If no student, return safe dummy sample (keeps UI working) ---
        if not student:
            categories = ["All"]
            sample = {
                "pdfs": [],
                "videos": [],
                "audio": [],
                "journals": [],
            }
            return Response({
                "categories": categories,
                "courses": [],
                "selected_course_id": None,
                "selected_module_id": None,
                **sample
            }, status=status.HTTP_200_OK)

        # --- User's active courses (categories) ---
        if course_ids is None:
            active_enrolls = (Enrollment.objects
                            .filter(Q(status=Enrollment.Status.ACTIVE)|Q(status=Enrollment.Status.COMPLETED),
                                student=student, course__is_active=True )
                            .select_related("course", "course__subject"))
            course_ids = list(active_enrolls.values_list("course_id", flat=True))
            
        if not course_ids:
            # No active enrollments → return empty with categories=[]
            return Response({
                "categories": [],
                "courses": [],
                "selected_course_id": None,
                "selected_module_id": None,
                "pdfs": [], "videos": [], "audio": [], "journals": []
            }, status=status.HTTP_200_OK)

        courses_qs = (Course.objects
                      .filter(id__in=course_ids)
                      .select_related("subject", "teacher__user", "classroom")
                      .order_by("name", "id"))
        courses = list(courses_qs)
        courses_meta = [{"id": c.id, "name": c.name} for c in courses]
        categories = [c.name for c in courses]  # UI currently expects an array of strings

        # Resolve selected course
        selected_course: Optional[Course] = None
        if course_id and any(c.id == course_id for c in courses):
            selected_course = next(c for c in courses if c.id == course_id)
        else:
            selected_course = courses[0]  # default: first active course

        # Resolve selected module (first active if none provided)
        modules_qs = (Module.objects
                      .filter(course=selected_course, active=True)
                      .order_by("order", "id"))
        if module_id:
            modules_qs = modules_qs.filter(id=module_id)

        # Use the first active module (after filter), else fall back to ANY active lesson under the course
        selected_module: Optional[Module] = modules_qs.first()

        # --- Base Lessons: active & in selected course (+module if picked) ---
        base_lessons = (Lesson.objects
                        .select_related("module", "module__course", "module__course__teacher__user", "module__course__subject")
                        .filter(
                            active=True,
                            module__active=True,
                            module__course_id=selected_course.id,
                        ))
        if selected_module:
            base_lessons = base_lessons.filter(module=selected_module)

        if q:
            base_lessons = base_lessons.filter(
                Q(name__icontains=q) |
                Q(module__course__name__icontains=q) |
                Q(module__course__subject__name__icontains=q)
            )

        # --- Partition by content_type ---
        vids_qs = base_lessons.filter(content_type=Lesson.ContentType.VIDEO).order_by("-updated_at", "module__order", "order")[:vids_limit]
        auds_qs = base_lessons.filter(content_type=Lesson.ContentType.AUDIO).order_by("-updated_at", "module__order", "order")[:auds_limit]
        pdfs_qs = base_lessons.filter(content_type=Lesson.ContentType.PDF).order_by("-updated_at", "module__order", "order")[:pdfs_limit]
        docs_qs = base_lessons.filter(content_type=Lesson.ContentType.DOC).order_by("-updated_at", "module__order", "order")[:journ_limit]

        # --- Build each section in the shape your UI expects (with dummy fallbacks where needed) ---
        # PDFs
        pdfs: List[Dict[str, Any]] = []
        for ls in pdfs_qs:
            blur = (returned_count == 2) and (allowed_course_ids is not None) and (ls.module.course_id not in allowed_course_ids)

            if blur:
                file = ""
            else:
                file =  _safe_file_or_url(request, ls) if ls.file else None
                if ls.url and file is None:
                    file = request.build_absolute_uri(ls.url) if ls.url.startswith('/') else ls.url
            pdfs.append({
                "id": str(ls.id),
                "title": ls.name,
                "author": getattr(getattr(selected_course.teacher, "user", None), "get_full_name", lambda: "")() or
                          getattr(getattr(selected_course.teacher, "user", None), "username", "Instructor"),
                "pages": int(ls.meta.get("pages", 0)) if isinstance(ls.meta, dict) else 0,
                "size": ls.meta.get("size_label", "—") if isinstance(ls.meta, dict) else "—",
                "rating": float(ls.meta.get("rating", 4.7)) if isinstance(ls.meta, dict) else 4.7,
                "downloads": int(ls.meta.get("downloads", 0)) if isinstance(ls.meta, dict) else 0,
                "category": selected_course.name,
                "pdfUrl": file,
                "blur":blur,
            })
        if not pdfs:
            pdfs = []

        # Videos
        videos: List[Dict[str, Any]] = []
        for ls in vids_qs:
            try:
                thumb_nail = request.build_absolute_uri(ls.cover_image.url) if ls.cover_image else None
            except ValueError:
                thumb_nail = None
            blur = (returned_count == 2) and (allowed_course_ids is not None) and (ls.module.course_id not in allowed_course_ids)

            if blur:
                file = ""
            else:
                if getattr(settings, "IS_LOCAL", False) and getattr(ls, 'file', None):
                    file = request.build_absolute_uri(f"/learning/api/stream-video/{ls.id}/")
                else:
                    file =  _safe_file_or_url(request, ls) if ls.file else None
                    if ls.url and file is None:
                        file = request.build_absolute_uri(ls.url) if ls.url.startswith('/') else ls.url
            videos.append({
                "id": str(ls.id),
                "title": ls.name,
                "instructor": getattr(getattr(selected_course.teacher, "user", None), "get_full_name", lambda: "")() or
                               getattr(getattr(selected_course.teacher, "user", None), "username", "Instructor"),
                "duration": _fmt_duration(ls.duration_seconds),
                "views": int(ls.meta.get("views", 0)) if isinstance(ls.meta, dict) else 0,
                "rating": float(ls.meta.get("rating", 4.7)) if isinstance(ls.meta, dict) else 4.7,
                "category": selected_course.name,
                "videoUrl": file,
                "thumbnail":thumb_nail,
                "blur":blur,
            })
        if not videos:
            videos = []

        # Audio
        audio: List[Dict[str, Any]] = []
        for ls in auds_qs:
            blur = (returned_count == 2) and (allowed_course_ids is not None) and (ls.module.course_id not in allowed_course_ids)

            if blur:
                file = ""
            else:
                if getattr(settings, "IS_LOCAL", False) and getattr(ls, 'file', None):
                    file = request.build_absolute_uri(f"/learning/api/stream-video/{ls.id}/")
                else:
                    file =  _safe_file_or_url(request, ls) if ls.file else None
                    if ls.url and file is None:
                        file = request.build_absolute_uri(ls.url) if ls.url.startswith('/') else ls.url
            audio.append({
                "id": str(ls.id),
                "title": ls.name,
                "speaker": getattr(getattr(selected_course.teacher, "user", None), "get_full_name", lambda: "")() or
                           getattr(getattr(selected_course.teacher, "user", None), "username", "Speaker"),
                "duration": _fmt_duration(ls.duration_seconds),
                "listens": int(ls.meta.get("listens", 0)) if isinstance(ls.meta, dict) else 0,
                "rating": float(ls.meta.get("rating", 4.5)) if isinstance(ls.meta, dict) else 4.5,
                "category": selected_course.name,
                "audioUrl":file,
                "blur":blur
            })
        if not audio:
            audio = []

        # Journals (Docs)
        journals: List[Dict[str, Any]] = []
        for ls in docs_qs:
            meta = ls.meta if isinstance(ls.meta, dict) else {}
            journals.append({
                "id": str(ls.id),
                "title": ls.name,
                "journal": meta.get("journal", "Course Docs"),
                "date": meta.get("date_label", timezone.now().strftime("%b %Y")),
                "pages": int(meta.get("pages", 0)),
                "citations": int(meta.get("citations", 0)),
                "category": selected_course.name,
                "content": meta.get("abstract") or meta.get("summary") or "—",
            })
        if not journals:
            journals = []

        response_data = {
            # categories for your chip list (strings)
            "categories": categories,
            # also return ids so you can wire filters later if needed
            "courses": courses_meta,
            "selected_course_id": selected_course.id if selected_course else None,
            "selected_module_id": selected_module.id if selected_module else None,
            # sections shaped for your UI
            "pdfs": pdfs,
            "videos": videos,
            "audio": audio,
            "journals": journals,
        }
        try:
            import json
            with open('resources_debug.json', 'w') as f:
                json.dump(response_data, f, indent=2)
        except Exception:
            pass

        return Response(response_data, status=status.HTTP_200_OK)

    except Exception as e:
        err = {"detail": "Failed to load resource materials.", "error": f"{type(e).__name__}: {e}"}
        if request.query_params.get("debug") in {"1", "true", "True"} or getattr(settings, "DEBUG", False):
            err["traceback"] = traceback.format_exc()
        return Response(err, status=status.HTTP_500_INTERNAL_SERVER_ERROR)





def _has_field(model, field: str) -> bool:
    """Check if model has a specific field."""
    try:
        model._meta.get_field(field)
        return True
    except Exception:
        return False


def _get_teacher_for_user(user) -> Optional[TeacherProfile]:
    """Get teacher profile for the authenticated user."""
    mem = (OrganizationMembership.objects
           .filter(user=user, is_active=True, role='teacher')
           .select_related("organization")
           .order_by("-id")
           .first())
    if mem:
        tp = TeacherProfile.objects.filter(user=user, organization=mem.organization).first()
        if tp:
            return tp
    return TeacherProfile.objects.filter(user=user).order_by("-id").first()


def _serialize_lesson(lesson: Lesson) -> Dict[str, Any]:
    """Convert lesson model to API response format."""
    if lesson.duration_seconds:
        duration_min = int(round(lesson.duration_seconds / 60))
    else:
        duration_min = 0
    return {
        "id": lesson.id,
        "title": lesson.name,
        "type": lesson.content_type,
        "order": lesson.order,
        "duration": duration_min,
        "file": lesson.file.url if lesson.file else None,
        "url": lesson.url or "",
        "videoUrl": lesson.url if lesson.content_type == "video" else "",
        "audioUrl": lesson.url if lesson.content_type == "audio" else "",
        "pdfUrl": lesson.url if lesson.content_type == "pdf" else "",
        "textContent": lesson.meta.get("text_content", "") if lesson.meta else "",
        "active": lesson.active,
        "meta": lesson.meta or {},
        "cover_image": lesson.cover_image.url if lesson.cover_image else None,
    }


def _serialize_module(module: Module, include_lessons: bool = False) -> Dict[str, Any]:
    """Convert module model to API response format."""
    data = {
        "id": module.id,
        "title": module.name,
        "description": module.description or "",
        "difficulty": module.difficulty,
        "category": {
            "id": module.category.id,
            "name": module.category.name
        } if module.category else None,
        "estimatedDuration": module.estimated_duration_in_minutes or 0,
        "duration": module.estimated_duration_in_minutes or 0,
        "order": module.order,
        "active": module.active,
        "isPublished": module.active,  # Using active as published status
        "course": {
            "id": module.course.id,
            "name": module.course.name
        } if module.course else None,
        "createdAt": module.created_at.isoformat() if hasattr(module, 'created_at') else None,
        "updatedAt": module.updated_at.isoformat() if hasattr(module, 'updated_at') else None,
        "freeze_code_submission":module.course.freeze_code_submission,
    }
    
    if include_lessons:
        lessons = module.lessons.filter(active=True).order_by('order', 'id')
        data["lessons"] = [_serialize_lesson(lesson) for lesson in lessons]
        data["lessonCount"] = lessons.count()
    
    return data


@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def list_modules(request):
    """
    List all modules for the authenticated teacher's courses.
    
    Query params:
      - course: course id to filter
      - category: category id to filter
      - active: '1'/'true' for only active, '0'/'false' for inactive, omit for all
      - difficulty: 'beginner' | 'intermediate' | 'advanced' (case-insensitive)
      - include_lessons: '1' to include lessons in response
      - debug: '1' to include traceback on error
    """
    try:
        user = request.user
        teacher = _get_teacher_for_user(user)
        if not teacher:
            return Response(
                {"modules": [], "detail": "No teacher profile found."},
                status=status.HTTP_200_OK,
            )

        # Get teacher's courses
        courses = Course.objects.filter(teacher=teacher, is_active=True)
        course_ids = list(courses.values_list("id", flat=True))

        if not course_ids:
            return Response({"modules": []}, status=status.HTTP_200_OK)

        # Start with all modules for teacher's courses
        qs = Module.objects.filter(course_id__in=course_ids)
        search_filter = request.query_params.get("search")
        if search_filter:
            s = search_filter.strip()
            if s:
                qs = qs.filter(Q(name__icontains=s) | Q(description__icontains=s))
        # Optional: filter by course id (accepts 'course' or 'course_id')
        course_filter = request.query_params.get("course") or request.query_params.get("course_id")
        if course_filter:
            try:
                qs = qs.filter(course_id=int(course_filter))
            except ValueError:
                # ignore invalid course id
                pass

        # Optional: filter by category id
        category_filter = request.query_params.get("category")
        if category_filter:
            try:
                qs = qs.filter(category_id=int(category_filter))
            except ValueError:
                # ignore invalid category id
                pass

        # Optional: filter by active status (accepts 1/0/true/false)
        active_filter = request.query_params.get("active")
        if active_filter is not None:
            normalized = active_filter.strip().lower()
            if normalized in {"1", "true", "t", "yes"}:
                qs = qs.filter(active=True)
            elif normalized in {"0", "false", "f", "no"}:
                qs = qs.filter(active=False)
            # else: ignore invalid values and don't filter by active

        # Optional: filter by difficulty (e.g., difficulty=intermediate)
        difficulty_filter = request.query_params.get("difficulty")
        if difficulty_filter:
            diff_value = difficulty_filter.strip().lower()
            qs = qs.filter(difficulty__iexact=diff_value)

        # Include lessons if requested
        include_lessons = request.query_params.get("include_lessons") == "1"
        if include_lessons:
            qs = qs.prefetch_related(
                Prefetch(
                    "lessons",
                    queryset=Lesson.objects.filter(active=True).order_by("order", "id"),
                )
            )

        # Order by course, then by order, then by id
        qs = qs.select_related("course", "category").order_by(
            "course__name", "order", "id"
        )

        modules = [_serialize_module(module, include_lessons) for module in qs]

        return Response({"modules": modules}, status=status.HTTP_200_OK)

    except Exception as e:
        payload = {
            "detail": "Error while fetching modules.",
            "error": f"{type(e).__name__}: {e}",
        }
        if request.query_params.get("debug") in {"1", "true", "True"} or getattr(
            settings, "DEBUG", False
        ):
            payload["traceback"] = traceback.format_exc()
        return Response(payload, status=status.HTTP_500_INTERNAL_SERVER_ERROR)



@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def get_module(request, module_id: int):
    """Get detailed information about a specific module including all lessons."""
    try:
        user = request.user
        teacher = _get_teacher_for_user(user)
        if not teacher:
            return Response({"detail": "No teacher profile found."}, status=status.HTTP_404_NOT_FOUND)

        try:
            module = Module.objects.select_related('course', 'category').prefetch_related(
                Prefetch('lessons', queryset=Lesson.objects.filter(active=True).order_by('order', 'id'))
            ).get(id=module_id, course__teacher=teacher)
        except Module.DoesNotExist:
            return Response({"detail": "Module not found."}, status=status.HTTP_404_NOT_FOUND)

        if module.active is False:
            return Response({"detail": "Module not active."}, status=status.HTTP_404_NOT_FOUND)

        module_data = _serialize_module(module, include_lessons=True)
        return Response({"module": module_data}, status=status.HTTP_200_OK)

    except Exception as e:
        payload = {
            "detail": "Error while fetching module.",
            "error": f"{type(e).__name__}: {e}",
        }
        if request.query_params.get("debug") in {"1", "true", "True"} or getattr(settings, "DEBUG", False):
            payload["traceback"] = traceback.format_exc()
        return Response(payload, status=status.HTTP_500_INTERNAL_SERVER_ERROR)



def _next_available_order(course, start_from: int = 1) -> int:
    # Get all existing orders once, then scan for the first gap >= start_from
    taken = set(
        Module.objects.filter(course=course).values_list("order", flat=True)
    )
    i = max(1, int(start_from or 1))
    while i in taken:
        i += 1
    return i

@api_view(["POST"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
@transaction.atomic
def create_module(request):
    """
    Create a new module.

    Expected JSON body:
    {
        "title": "Module Title",
        "description": "Module description",
        "courseId": 123,      // or "course_id"
        "difficulty": "BEGINNER",
        "categoryId": 456,
        "estimatedDuration": 60,
        "order": 1
    }
    """
    try:
        user = request.user
        teacher = _get_teacher_for_user(user)
        if not teacher:
            return Response({"detail": "No teacher profile found."}, status=status.HTTP_404_NOT_FOUND)

        data = request.data or {}

        # Validate required fields
        title = (data.get("title") or "").strip()
        if not title:
            return Response({"detail": "Title is required."}, status=status.HTTP_400_BAD_REQUEST)

        # Accept either courseId or course_id
        course_id = data.get("course_id") or data.get("courseId")
        if not course_id:
            return Response({"detail": "Course ID is required."}, status=status.HTTP_400_BAD_REQUEST)

        # Verify teacher owns the course
        try:
            course = Course.objects.get(id=course_id, teacher=teacher, is_active=True)
        except Course.DoesNotExist:
            return Response({"detail": "Course not found or access denied."}, status=status.HTTP_404_NOT_FOUND)

        # Get category if provided
        category = None
        category_id = data.get("categoryId")
        if category_id:
            try:
                category = ModuleCategory.objects.get(id=category_id, active=True)
            except ModuleCategory.DoesNotExist:
                return Response({"detail": "Category not found."}, status=status.HTTP_404_NOT_FOUND)

        # Figure out desired starting order (if provided)
        requested_order = data.get("order")
        try:
            requested_order = int(requested_order) if requested_order is not None else None
        except (TypeError, ValueError):
            return Response({"detail": "Order must be an integer."}, status=status.HTTP_400_BAD_REQUEST)

        # If order not provided, start from (last+1); if provided, start from that number
        if requested_order is None:
            last = Module.objects.filter(course=course).order_by("-order").first()
            start_from = (last.order + 1) if last else 1
        else:
            start_from = max(1, requested_order)

        # Pick the first available order slot >= start_from
        order = _next_available_order(course, start_from=start_from)

        # Create module (retry once more if a concurrent insert sneaks in)
        for _ in range(2):
            try:
                module = Module.objects.create(
                    name=title,
                    description=data.get("description", ""),
                    course=course,
                    difficulty=data.get("difficulty", Module.DifficultyLevel.BEGINNER),
                    category=category,
                    estimated_duration_in_minutes=(data.get("estimatedDuration") or None),
                    order=order,
                    active=True,
                )
                break
            except IntegrityError:
                # In case of race condition on (course, order), bump to next free slot
                order = _next_available_order(course, start_from=order + 1)
        else:
            return Response({"detail": "Could not allocate a unique order, please retry."},
                            status=status.HTTP_409_CONFLICT)

        module_data = _serialize_module(module, include_lessons=True)
        return Response({"module": module_data}, status=status.HTTP_201_CREATED)

    except Exception as e:
        payload = {
            "detail": "Error while creating module.",
            "error": f"{type(e).__name__}: {e}",
        }
        if request.query_params.get("debug") in {"1", "true", "True"} or getattr(settings, "DEBUG", False):
            payload["traceback"] = traceback.format_exc()
        return Response(payload, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["PUT", "PATCH"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
@transaction.atomic
def update_module(request, module_id: int):
    """Update an existing module."""
    try:
        user = request.user
        teacher = _get_teacher_for_user(user)
        if not teacher:
            return Response(
                {"detail": "No teacher profile found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            module = Module.objects.get(id=module_id, course__teacher=teacher)
        except Module.DoesNotExist:
            return Response(
                {"detail": "Module not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        data = request.data or {}

        # ---- Title ----
        if "title" in data:
            title = (data["title"] or "").strip()
            if not title:
                return Response(
                    {"detail": "Title cannot be empty."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            module.name = title

        # ---- Description ----
        if "description" in data:
            module.description = data["description"]

        # ---- Difficulty ----
        if "difficulty" in data and data["difficulty"]:
            valid_values = [choice[0].lower() for choice in Module.DifficultyLevel.choices]
            diff_value = str(data["difficulty"]).lower()
            if diff_value in valid_values:
                module.difficulty = diff_value

        # ---- Estimated duration (minutes) ----
        if "estimatedDuration" in data:
            # allow null / empty to clear
            value = data["estimatedDuration"]
            module.estimated_duration_in_minutes = value or None

        # ---- Order ----
        if "order" in data and data["order"] is not None:
            module.order = data["order"]

        # ---- Category ----
        if "categoryId" in data:
            category_id = data["categoryId"]
            if category_id:
                try:
                    category = ModuleCategory.objects.get(
                        id=category_id,
                        active=True,
                    )
                    module.category = category
                except ModuleCategory.DoesNotExist:
                    return Response(
                        {"detail": "Category not found."},
                        status=status.HTTP_404_NOT_FOUND,
                    )
            else:
                # explicit null / empty => clear category
                module.category = None

        # ---- Course ----
        if "course_id" in data:
            course_id = data["course_id"]
            if course_id:
                try:
                    course = Course.objects.get(id=course_id, teacher=teacher)
                except Course.DoesNotExist:
                    return Response(
                        {"detail": "Course not found."},
                        status=status.HTTP_404_NOT_FOUND,
                    )
                module.course = course

        module.save()

        # Refresh from DB with relations
        module = (
            Module.objects.select_related("course", "category")
            .prefetch_related(
                Prefetch(
                    "lessons",
                    queryset=Lesson.objects.filter(active=True).order_by("order", "id"),
                )
            )
            .get(id=module_id)
        )

        module_data = _serialize_module(module, include_lessons=True)
        return Response({"module": module_data}, status=status.HTTP_200_OK)

    except Exception as e:
        payload = {
            "detail": "Error while updating module.",
            "error": f"{type(e).__name__}: {e}",
        }
        if request.query_params.get("debug") in {"1", "true", "True"} or getattr(
            settings, "DEBUG", False
        ):
            payload["traceback"] = traceback.format_exc()
        return Response(payload, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(["DELETE"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
@transaction.atomic
def delete_module(request, module_id: int):
    """Delete a module (soft delete by setting active=False)."""
    try:
        user = request.user
        teacher = _get_teacher_for_user(user)
        if not teacher:
            return Response({"detail": "No teacher profile found."}, status=status.HTTP_404_NOT_FOUND)

        try:
            module = Module.objects.get(id=module_id, course__teacher=teacher)
        except Module.DoesNotExist:
            return Response({"detail": "Module not found."}, status=status.HTTP_404_NOT_FOUND)

        # Soft delete - set active to False
        module.active = False
        module.save()
        
        # Also deactivate all lessons in this module
        Lesson.objects.filter(module=module).update(active=False)

        return Response({"detail": "Module deleted successfully."}, status=status.HTTP_200_OK)

    except Exception as e:
        payload = {
            "detail": "Error while deleting module.",
            "error": f"{type(e).__name__}: {e}",
        }
        if request.query_params.get("debug") in {"1", "true", "True"} or getattr(settings, "DEBUG", False):
            payload["traceback"] = traceback.format_exc()
        return Response(payload, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["POST"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
@transaction.atomic
def publish_module(request, module_id: int):
    """
    Publish or unpublish a module.
    
    Expected JSON body:
    {
        "published": true/false
    }
    """
    try:
        user = request.user
        teacher = _get_teacher_for_user(user)
        if not teacher:
            return Response({"detail": "No teacher profile found."}, status=status.HTTP_404_NOT_FOUND)

        try:
            module = Module.objects.get(id=module_id, course__teacher=teacher)
        except Module.DoesNotExist:
            return Response({"detail": "Module not found."}, status=status.HTTP_404_NOT_FOUND)

        data = request.data or {}
        published = data.get("published", True)
        
        module.active = bool(published)
        module.save()

        action = "published" if published else "unpublished"
        return Response({
            "detail": f"Module {action} successfully.",
            "published": module.active
        }, status=status.HTTP_200_OK)

    except Exception as e:
        payload = {
            "detail": "Error while publishing module.",
            "error": f"{type(e).__name__}: {e}",
        }
        if request.query_params.get("debug") in {"1", "true", "True"} or getattr(settings, "DEBUG", False):
            payload["traceback"] = traceback.format_exc()
        return Response(payload, status=status.HTTP_500_INTERNAL_SERVER_ERROR)



@api_view(["GET"])
@permission_classes([IsAuthenticated])  # ✅ ADD
@authentication_classes([SessionTokenAuthentication])  # ✅ ADD
def cloudinary_signature(request):
    timestamp = int(time.time())
    folder = "texagon/lessons"
    params_to_sign = {"timestamp": timestamp, "folder": folder}

    signature = cloudinary.utils.api_sign_request(
        params_to_sign,
        os.getenv("CLOUDINARY_API_SECRET")
    )

    return JsonResponse({
        "timestamp": timestamp,
        "signature": signature,
        "api_key": os.getenv("CLOUDINARY_API_KEY"),
        "cloud_name": os.getenv("CLOUDINARY_CLOUD_NAME"),
        "folder": folder,
    })


@api_view(["POST"])
@permission_classes([IsAuthenticated])
@authentication_classes([SessionTokenAuthentication])
@parser_classes([MultiPartParser, FormParser, JSONParser])
def presign_s3(request):
    # ── LOCAL MODE: save file directly to local filesystem ──
    if getattr(settings, "IS_LOCAL", False):
        uploaded_file = request.FILES.get("file")
        filename = (request.data.get("filename") or "").strip()

        if not uploaded_file and not filename:
            return Response({"detail": "file or filename is required"}, status=status.HTTP_400_BAD_REQUEST)

        if uploaded_file:
            from django.core.files.storage import default_storage
            name = uploaded_file.name
            base, ext = os.path.splitext(name)
            safe_base = re.sub(r"[^\w\-]", "_", base.strip()).strip("_") or "file"
            safe_base = re.sub(r"_+", "_", safe_base)
            safe_name = f"lessons/{safe_base}{ext}"
            saved_path = default_storage.save(safe_name, uploaded_file)
            return Response({
                "upload_url": None,
                "key": saved_path,
                "filename": os.path.basename(saved_path),
                "mode": "local"
            })
        else:
            # Filename-only request — return key for multipart upload
            base, ext = os.path.splitext(filename)
            safe_base = re.sub(r"[^\w\-]", "_", base.strip()).strip("_") or "file"
            safe_base = re.sub(r"_+", "_", safe_base)
            return Response({
                "upload_url": None,
                "key": f"lessons/{safe_base}{ext}",
                "filename": f"{safe_base}{ext}",
                "mode": "local"
            })

    # ── S3 MODE (production) ──
    uploaded_file = request.FILES.get("file")
    filename = (request.data.get("filename") or (uploaded_file.name if uploaded_file else "")).strip()
    content_type = (request.data.get("content_type") or getattr(uploaded_file, "content_type", "application/octet-stream") or "application/octet-stream").strip()

    if not filename:
        return Response({"detail": "filename is required"}, status=status.HTTP_400_BAD_REQUEST)

    # Preserve original filename — split name and extension
    base, ext = os.path.splitext(filename)
    ext = ext.lower()

    # Sanitize the base name: lowercase, replace spaces/special chars with underscores
    safe_base = re.sub(r"[^\w\-]", "_", base.strip()).strip("_") or "file"
    safe_base = re.sub(r"_+", "_", safe_base)  # collapse consecutive underscores

    s3 = boto3.client(
        "s3",
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        region_name=settings.AWS_S3_REGION_NAME,
        endpoint_url=getattr(settings, "AWS_S3_ENDPOINT_URL", None),
    )

    # Build a unique key — append a counter if the object already exists in S3
    from botocore.exceptions import ClientError as BotoCoreClientError

    candidate_key = f"lessons/{safe_base}{ext}"
    counter = 1
    while True:
        try:
            s3.head_object(Bucket=settings.AWS_STORAGE_BUCKET_NAME, Key=candidate_key)
            # Object exists — try the next suffix
            candidate_key = f"lessons/{safe_base}_{counter}{ext}"
            counter += 1
        except BotoCoreClientError as e:
            # 404 → key is free; anything else (e.g. 403) → key is free too
            # (we still generate the presigned URL; S3 enforces permissions on upload)
            break

    key = candidate_key

    if uploaded_file:
        s3.upload_fileobj(
            uploaded_file,
            settings.AWS_STORAGE_BUCKET_NAME,
            key,
            ExtraArgs={"ContentType": content_type},
        )
        return Response({"upload_url": None, "key": key, "filename": os.path.basename(key)})

    upload_url = s3.generate_presigned_url(
        ClientMethod="put_object",
        Params={
            "Bucket": settings.AWS_STORAGE_BUCKET_NAME,
            "Key": key,
            "ContentType": content_type,
        },
        ExpiresIn=60 * 15,  # 15 mins to upload
    )

    return Response({"upload_url": upload_url, "key": key, "filename": os.path.basename(key)})



@api_view(["GET"])
@permission_classes([IsAuthenticated])
@authentication_classes([SessionTokenAuthentication])  # ✅ ADD
def lesson_media_url(request, lesson_id: int):
    from .models import Lesson

    lesson = Lesson.objects.filter(id=lesson_id).first()
    if not lesson:
        return Response({"detail": "Lesson not found."}, status=status.HTTP_404_NOT_FOUND)

    # For students, enforce general activation check if subscription has expired
    student = getattr(request.user, "student_profile", None)
    if student:
        data, returned_count = student.get_course_allowed(request, is_session=True)
        if returned_count == 2:
            if lesson.module.course_id not in data[0]:
                return Response({"detail": "Course access has expired. Please renew your subscription."}, status=status.HTTP_403_FORBIDDEN)

    if lesson.file:
        try:
            if getattr(settings, "IS_LOCAL", False):
                return Response({"url": request.build_absolute_uri(f"/learning/api/stream-video/{lesson.id}/")})
            return Response({"url": request.build_absolute_uri(lesson.file.url)})
        except ValueError:
            pass

    value = (lesson.url or "").strip()
    if not value:
        return Response({"detail": "No media set for this lesson"}, status=status.HTTP_400_BAD_REQUEST)

    # ✅ Cloudinary (or any direct URL) → return as-is
    if value.startswith("http://") or value.startswith("https://"):
        return Response({"url": value})

    # ✅ Otherwise treat as storage key
    key = value

    # -------- LOCAL MODE: return local media URL --------
    if getattr(settings, "IS_LOCAL", False):
        return Response({"url": request.build_absolute_uri(f"/learning/api/stream-video/{lesson.id}/")})

    # ── S3 MODE: presign GET ──
    s3 = boto3.client(
        "s3",
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        region_name=settings.AWS_S3_REGION_NAME,
        endpoint_url=getattr(settings, "AWS_S3_ENDPOINT_URL", None),
    )

    signed_get = s3.generate_presigned_url(
        ClientMethod="get_object",
        Params={"Bucket": settings.AWS_STORAGE_BUCKET_NAME, "Key": key},
        ExpiresIn=60 * 60,
    )
    return Response({"url": signed_get})

@api_view(["POST"])
@permission_classes([IsAuthenticated])
@authentication_classes([SessionTokenAuthentication])
@parser_classes([MultiPartParser, FormParser, JSONParser])
@transaction.atomic
def add_lesson(request, module_id: int):
    """
    Add a new lesson to a module.

    Accepts multipart/form-data. Use field name 'file' for the uploaded file.

    Example form fields:
      - title (required)
      - type (optional) (video|audio|pdf|doc|link)
      - duration (optional) (seconds)
      - url (optional)
      - order (optional)
      - meta (optional) JSON string or JSON object
      - file (optional) file upload
    """
    try:
        user = request.user
        teacher = _get_teacher_for_user(user)
        if not teacher:
            return Response({"detail": "No teacher profile found."}, status=status.HTTP_404_NOT_FOUND)

        try:
            module = Module.objects.get(id=module_id, course__teacher=teacher)
        except Module.DoesNotExist:
            return Response({"detail": "Module not found."}, status=status.HTTP_404_NOT_FOUND)

        data = request.data or {}

        # Validate required fields
        title = (data.get("title") or "").strip()
        if not title:
            return Response({"detail": "Title is required."}, status=status.HTTP_400_BAD_REQUEST)

        # Determine content type (validate choice)
        content_type = data.get("type", "video")
        if content_type not in [choice[0] for choice in Lesson.ContentType.choices]:
            content_type = "video"

        # Get next order number if not provided
        order = data.get("order")
        if order in (None, ""):
            last_lesson = Lesson.objects.filter(module=module).order_by('-order').first()
            order = (last_lesson.order + 1) if last_lesson else 1
        else:
            try:
                order = int(order)
            except (TypeError, ValueError):
                order = 1

        # Parse duration
        duration_seconds = 0
        duration_str = data.get("duration", "") or ""
        if duration_str != "":
            try:
                duration_seconds = int(duration_str)
            except ValueError:
                duration_seconds = 0

        # Parse meta if provided as string (multipart forms often send JSON as string)
        meta = data.get("meta", {}) or {}
        if isinstance(meta, str) and meta:
            try:
                meta = json.loads(meta)
            except json.JSONDecodeError:
                # leave as string or empty dict
                meta = {}

        # Handle file upload (if any)
        uploaded_file = None
        cover_image = None 
        if request.FILES:
            uploaded_file = request.FILES.get("file")  # field name 'file'
            cover_image = request.FILES.get("cover_image")
            if uploaded_file:
                # attempt to set content_type based on extension if user didn't provide a suitable type
                name = uploaded_file.name or ""
                _, ext = os.path.splitext(name)
                ext = ext.lower().lstrip(".")
                ext_to_type = {
                    # video
                    "mp4": "video", "mov": "video", "avi": "video", "mkv": "video", "webm": "video",
                    # audio
                    "mp3": "audio", "wav": "audio", "m4a": "audio", "aac": "audio",
                    # pdf
                    "pdf": "pdf",
                    # documents
                    "doc": "doc", "docx": "doc", "txt": "doc", "rtf": "doc", "odt": "doc",
                }
                guessed = ext_to_type.get(ext)
                if guessed and guessed in [choice[0] for choice in Lesson.ContentType.choices]:
                    content_type = guessed
        
        # Create lesson (assign file if provided)
        s3_key = (data.get("s3_key") or "").strip()  # ✅ ADD
        direct_url = (data.get("url") or "").strip() # ✅ ADD
        lesson = Lesson(
            name=title,
            module=module,
            order=order,
            content_type=content_type,
            url=s3_key or direct_url,
            duration_seconds=duration_seconds,
            meta=meta,
            active=True
        )
        if uploaded_file:
            lesson.file = uploaded_file

        if cover_image:
            lesson.cover_image = cover_image

        lesson.save()

        lesson_data = _serialize_lesson(lesson)
        return Response({"lesson": lesson_data}, status=status.HTTP_201_CREATED)

    except Exception as e:
        payload = {
            "detail": "Error while adding lesson.",
            "error": f"{type(e).__name__}: {e}",
        }
        if request.query_params.get("debug") in {"1", "true", "True"} or getattr(settings, "DEBUG", False):
            payload["traceback"] = traceback.format_exc()
        return Response(payload, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["PUT", "PATCH"])
@permission_classes([IsAuthenticated])
@authentication_classes([SessionTokenAuthentication])
@parser_classes([MultiPartParser, FormParser, JSONParser])
@transaction.atomic
def update_lesson(request, module_id: int, lesson_id: int):
    """Update an existing lesson. Supports uploading a new file (field 'file') or removing existing file."""
    try:
        user = request.user
        teacher = _get_teacher_for_user(user)
        if not teacher:
            return Response({"detail": "No teacher profile found."}, status=status.HTTP_404_NOT_FOUND)

        try:
            lesson = Lesson.objects.get(
                id=lesson_id,
                module_id=module_id,
                module__course__teacher=teacher,
                active =True
            )
        except Lesson.DoesNotExist:
            return Response({"detail": "Lesson not found."}, status=status.HTTP_404_NOT_FOUND)

        data = request.data or {}

        # Update fields if provided
        if "title" in data:
            title = (data["title"] or "").strip()
            if not title:
                return Response({"detail": "Title cannot be empty."}, status=status.HTTP_400_BAD_REQUEST)
            lesson.name = title

        if "type" in data:
            content_type = data["type"]
            if content_type in [choice[0] for choice in Lesson.ContentType.choices]:
                lesson.content_type = content_type
        s3_key = (data.get("s3_key") or "").strip()  # ✅ ADD
        direct_url = (data.get("url") or "").strip() # ✅ ADD
        if "url" in data:
            lesson.url = s3_key if s3_key else (data.get("url", "") or "")

        if "duration" in data:
            try:
                lesson.duration_seconds = int(data["duration"]) if data["duration"] else 0
            except ValueError:
                pass

        if "order" in data and data["order"] not in (None, ""):
            try:
                lesson.order = int(data["order"])
            except (TypeError, ValueError):
                pass

        if "meta" in data:
            meta = data.get("meta", {})
            if isinstance(meta, str) and meta:
                try:
                    meta = json.loads(meta)
                except json.JSONDecodeError:
                    meta = {}
            lesson.meta = meta
        lesson.save()
        # Remove file if requested (send remove_file=true in form-data or JSON)
        remove_file = str(data.get("remove_file", "")).lower() in ("1", "true", "yes")
        if remove_file and lesson.file:
            # delete file from storage and clear field
            lesson.file.delete(save=False)
            lesson.file = None

        # Handle uploaded file (replace existing)
        if request.FILES:

            uploaded_file = request.FILES.get("file")
            cover_image = request.FILES.get("cover_image")
            if cover_image:
                if lesson.cover_image:
                    lesson.cover_image.delete(save=False)
                lesson.cover_image = cover_image
                lesson.save()
                
            if uploaded_file:
                # optionally remove previous file
                if lesson.file:
                    lesson.file.delete(save=False)
                lesson.file = uploaded_file
                lesson.save()

                # attempt to update content_type from extension if sensible
                name = uploaded_file.name or ""
                _, ext = os.path.splitext(name)
                ext = ext.lower().lstrip(".")
                ext_to_type = {
                    "mp4": "video", "mov": "video", "avi": "video", "mkv": "video", "webm": "video",
                    "mp3": "audio", "wav": "audio", "m4a": "audio", "aac": "audio",
                    "pdf": "pdf",
                    "doc": "doc", "docx": "doc", "txt": "doc", "rtf": "doc", "odt": "doc",
                }
                guessed = ext_to_type.get(ext)
                if guessed and guessed in [choice[0] for choice in Lesson.ContentType.choices]:
                    lesson.content_type = guessed
                    
        remove_cover = str(data.get("remove_cover", "")).lower() in ("1", "true", "yes")
        if remove_cover and getattr(lesson, "cover_image", None):
            lesson.cover_image.delete(save=False)
            lesson.cover_image = None
            lesson.save()

        lesson_data = _serialize_lesson(lesson)
        return Response({"lesson": lesson_data}, status=status.HTTP_200_OK)

    except Exception as e:
        payload = {
            "detail": "Error while updating lesson.",
            "error": f"{type(e).__name__}: {e}",
        }
        if request.query_params.get("debug") in {"1", "true", "True"} or getattr(settings, "DEBUG", False):
            payload["traceback"] = traceback.format_exc()
        return Response(payload, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["DELETE"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
@transaction.atomic
def delete_lesson(request, module_id: int, lesson_id: int):
    """Delete a lesson (soft delete by setting active=False)."""
    try:
        user = request.user
        teacher = _get_teacher_for_user(user)
        if not teacher:
            return Response({"detail": "No teacher profile found."}, status=status.HTTP_404_NOT_FOUND)

        try:
            lesson = Lesson.objects.get(
                id=lesson_id, 
                module_id=module_id, 
                module__course__teacher=teacher
            )
        except Lesson.DoesNotExist:
            return Response({"detail": "Lesson not found."}, status=status.HTTP_404_NOT_FOUND)

        # Soft delete - set active to False
        lesson.active = False
        lesson.save()

        return Response({"detail": "Lesson deleted successfully."}, status=status.HTTP_200_OK)

    except Exception as e:
        payload = {
            "detail": "Error while deleting lesson.",
            "error": f"{type(e).__name__}: {e}",
        }
        if request.query_params.get("debug") in {"1", "true", "True"} or getattr(settings, "DEBUG", False):
            payload["traceback"] = traceback.format_exc()
        return Response(payload, status=status.HTTP_500_INTERNAL_SERVER_ERROR)




@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def get_teacher_courses(request):
    """Get all courses for the authenticated teacher."""
    try:
        user = request.user
        teacher = _get_teacher_for_user(user)

        if not teacher:
            return Response(
                {"courses": [], "detail": "No teacher profile found."},
                status=status.HTTP_200_OK
            )

        # ✅ Read course_type from query params
        course_type = request.query_params.get("course_type")

        # Base queryset
        courses = Course.objects.filter(
            teacher=teacher,
            is_active=True,
        ).select_related("subject", "classroom")

        # ✅ Apply course_type filter if provided
        if course_type in dict(Course.USAGE_CHOICE):
            courses = courses.filter(course_type=course_type)

        courses_data = [
            {
                "id": course.id,
                "name": course.name,
                "subject": course.subject.name if course.subject else None,
                "classroom": course.classroom.name if course.classroom else None,
                "description": course.description,
                "isActive": course.is_active,
                "course_type": course.course_type,
                "freeze_code_submission":course.freeze_code_submission,
                "general_activation":course.general_activation,
                "general_activation_date": course.general_activation_date.isoformat() if course.general_activation_date else None

            }
            for course in courses
        ]

        return Response(
            {"courses": courses_data},
            status=status.HTTP_200_OK
        )

    except Exception as e:
        payload = {
            "detail": "Error while fetching courses.",
            "error": f"{type(e).__name__}: {e}",
        }

        if request.query_params.get("debug") in {"1", "true", "True"} or getattr(settings, "DEBUG", False):
            payload["traceback"] = traceback.format_exc()

        return Response(payload, status=status.HTTP_500_INTERNAL_SERVER_ERROR)



@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def get_module_categories(request):
    """Get all active module categories."""
    try:
        categories = ModuleCategory.objects.filter(active=True).order_by('name')
        
        categories_data = []
        for category in categories:
            categories_data.append({
                "id": category.id,
                "name": category.name
            })

        return Response({"categories": categories_data}, status=status.HTTP_200_OK)

    except Exception as e:
        payload = {
            "detail": "Error while fetching categories.",
            "error": f"{type(e).__name__}: {e}",
        }
        if request.query_params.get("debug") in {"1", "true", "True"} or getattr(settings, "DEBUG", False):
            payload["traceback"] = traceback.format_exc()
        return Response(payload, status=status.HTTP_500_INTERNAL_SERVER_ERROR)





@api_view(["DELETE"])
@permission_classes([HasAPIKey & RequiresActiveStudentSubscription()])
@authentication_classes([SessionTokenAuthentication])
@transaction.atomic
def delete_saved_material(request):
    """
    Delete a saved Material for the authenticated user (idempotent).

    DELETE /api/materials/delete/

    Accepts identifier in either:
      - query params: ?material_id=12   OR ?lesson_id=55
      - JSON body (DELETE): {"material_id": 12} OR {"lesson_id": 55}

    Rules:
      - Only deletes materials owned by the authenticated user.
      - If lesson_id is provided, deletes the user's Material linked to that lesson.
      - Returns 200 even if the record is already missing (detail='not_found') to keep it idempotent.
    """
    try:
        user = request.user

        # ---- read ids from query or body ----
        material_id = request.query_params.get("material_id") or request.query_params.get("id")
        lesson_id = request.query_params.get("lesson_id")

        body = {}
        try:
            # DRF parses body for DELETE if JSON; safe fallback
            body = request.data or {}
        except Exception:
            body = {}

        material_id = body.get("material_id") if material_id is None else material_id
        lesson_id = body.get("lesson_id") if lesson_id is None else lesson_id

        # ---- coerce ints ----
        def _to_int(v):
            try:
                return int(v)
            except Exception:
                return None

        material_id = _to_int(material_id)
        lesson_id = _to_int(lesson_id)

        if not material_id and not lesson_id:
            return Response(
                {"detail": "Provide material_id or lesson_id."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        qs = Material.objects.select_for_update().filter(owner=user)

        if material_id:
            qs = qs.filter(id=material_id)
        else:
            qs = qs.filter(lesson_id=lesson_id)

        m = qs.first()
        if not m:
            return Response(
                {"detail": "not_found", "deleted": False},
                status=status.HTTP_200_OK,
            )

        deleted_id = m.id
        deleted_lesson_id = m.lesson_id

        m.delete()

        return Response(
            {
                "detail": "deleted",
                "deleted": True,
                "material_id": deleted_id,
                "lesson_id": deleted_lesson_id,
            },
            status=status.HTTP_200_OK,
        )

    except Exception as e:
        import traceback
        err = {"detail": "Failed to delete saved material.", "error": f"{type(e).__name__}: {e}"}
        if request.query_params.get("debug") in {"1", "true", "True"}:
            err["traceback"] = traceback.format_exc()
        return Response(err, status=status.HTTP_500_INTERNAL_SERVER_ERROR)



@api_view(["GET"])
@permission_classes([HasAPIKey & RequiresActiveStudentSubscription()])
@authentication_classes([SessionTokenAuthentication])
def my_courses(request):
    try:
        from billing.models import UserAccountSubscription
        from django.utils import timezone

        student = _get_student_for_user(request.user)
        if not student:
            return Response({"detail": "Student profile not found."}, status=status.HTTP_404_NOT_FOUND)

        subscription_active = UserAccountSubscription.has_subscription(student.user, student.organization)
        now = timezone.now()

        def _course_to_dict(course):
            modules = [{"id": m.id, "name": m.name} for m in course.modules.filter(active=True).order_by("order")]
            has_course_general_access = (
                course.course_type == "public" and
                course.general_activation and
                (course.general_activation_date is None or course.general_activation_date > now)
            )
            has_access = subscription_active or has_course_general_access
            return {
                "id": course.id,
                "name": course.name,
                "modules": modules,
                "general_activation": course.general_activation,
                "general_activation_date": course.general_activation_date.isoformat() if course.general_activation_date else None,
                "has_access": has_access,
            }

        enrollments = (
            Enrollment.objects
            .select_related("course")
            .prefetch_related("course__modules")
            .filter(
                student=student,
                status__in=[Enrollment.Status.ACTIVE, Enrollment.Status.COMPLETED],
                course__is_active=True,   # optional: hide inactive courses
            )
            .order_by("course__name")
        )

        courses = [_course_to_dict(e.course) for e in enrollments]
    except Exception as e:
        import traceback
        if getattr(settings, "DEBUG", False):
            print(traceback.format_exc())
        return Response({}, status=status.HTTP_404_NOT_FOUND)
    return Response({"courses": courses}, status=status.HTTP_200_OK)




@api_view(["GET"])
@permission_classes([HasAPIKey & RequiresActiveStudentSubscription()])
@authentication_classes([SessionTokenAuthentication])
def my_lessons(request):
    student = _get_student_for_user(request.user)
    data, returned_count = student.get_course_allowed(request, is_session=True) if student else (([], None), 0)
    allowed_course_ids = data[0] if returned_count == 2 else None

    lessons = (
        Lesson.objects.filter(
            module__course__enrollments__student=student,
            module__course__enrollments__status__in=[Enrollment.Status.ACTIVE, Enrollment.Status.COMPLETED],
            active=True,
            module__active=True,
        )
        .distinct()
        .select_related("module", "module__course")
        .order_by("module__course_id", "module__order", "order")
    )

    data = LessonSerializer(lessons, many=True).data
    for item in data:
        course_id = item.get("course_id")
        blur = False
        if returned_count == 2 and allowed_course_ids is not None:
            blur = course_id not in allowed_course_ids
        item["blur"] = blur
    return Response(data)




@api_view(["PATCH"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def update_course_general_activation(request, course_id: int):
    """
    Teacher can update general_activation + general_activation_date for their own course.
    """
    teacher = _get_teacher_for_user(request.user)
    if not teacher:
        return Response({"detail": "No teacher profile found."}, status=status.HTTP_403_FORBIDDEN)

    course = (Course.objects
              .filter(id=course_id, teacher=teacher)
              .first())
    if not course:
        return Response({"detail": "Course not found for this teacher."}, status=status.HTTP_404_NOT_FOUND)

    if course.course_type == "private":
        return Response(
            {"detail": "General activation is not allowed for private courses."},
            status=status.HTTP_400_BAD_REQUEST
        )
        
    ser = CourseGeneralActivationSerializer(course, data=request.data, partial=True)
    if not ser.is_valid():
        return Response(ser.errors, status=status.HTTP_400_BAD_REQUEST)

    ser.save()
    return Response(
        {
            "id": course.id,
            "general_activation": course.general_activation,
            "general_activation_date": course.general_activation_date.isoformat() if course.general_activation_date else None,
        },
        status=status.HTTP_200_OK
    )


@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def toggle_code_submit(request):
    teacher = _get_teacher_for_user(request.user)
    if not teacher:
        return Response({"detail": "No teacher profile found."}, status=status.HTTP_403_FORBIDDEN)

    course_id = request.query_params.get("course_id")
    course = (Course.objects
              .filter(id=course_id, teacher=teacher)
              .first())
    if not course:
        return Response({"detail": "Course not found for this teacher."}, status=status.HTTP_404_NOT_FOUND)
    
    if course.freeze_code_submission:
        course.freeze_code_submission = False
    else:
        course.freeze_code_submission = True
    
    course.save()
    return Response(
        {
            "id": course.id,
        },
        status=status.HTTP_200_OK
    )

