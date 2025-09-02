# views.py
from typing import Optional, Dict, Any, List
import traceback

from django.conf import settings
from django.db.models import Q, Count, Max
from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.response import Response
from rest_framework import status
from rest_framework_api_key.permissions import HasAPIKey

from api.authentication import SessionTokenAuthentication  # your existing class
from orgs.models import OrganizationMembership
from academics.models import StudentProfile
from learning.models import (
    Module, Lesson, Course, Enrollment,
)

# --- Reuse this helper everywhere ---
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


@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def active_modules_for_user(request):
    """
    Return all *active* Modules connected to the authenticated student.

    Scoping:
      - Only Modules with Module.active=True
      - Only Modules belonging to Courses the student is actively enrolled in
      - Course.is_active is also respected (optional, toggle with ?only_active_courses=0 to ignore)

    Query params:
      - q: text search (module name, course name, subject)
      - course_id: filter to a specific course
      - subject_id: filter to a subject
      - teacher_id: filter to a teacher profile id
      - page (default 1), page_size (default 20, max 100)
      - only_active_courses (default 1): if 1, restrict to Course.is_active=True
      - debug=1 to include traceback in error responses

    Response:
      {
        "count": <int>,
        "page": <int>,
        "page_size": <int>,
        "results": [
          {
            "id": <module_id>,
            "name": "...",
            "order": <int>,
            "active": true,
            "course": {
              "id": <int>, "name": "...", "subject": "...",
              "classroom": "...", "teacher": "Display Name"
            },
            "lessons_count": <int>,             # active lessons in the module
            "last_updated": "ISO8601 or null",   # last lesson update in the module
            "course_progress": <int>,            # % from Enrollment.progress_pct
            "recent_lesson": {                   # first lesson by order (active) if any
              "id": <int>,
              "title": "...",
              "content_type": "video|audio|pdf|doc|link",
              "duration_seconds": <int>,
              "url": "file/url/or/external/url"
            }
          },
          ...
        ]
      }
    """
    try:
        user = request.user
        student = _get_student_for_user(user)
        if not student:
            return Response(
                {"count": 0, "page": 1, "page_size": 0, "results": [], "detail": "Student profile not found."},
                status=status.HTTP_200_OK,
            )

        # ----------- parsing filters / pagination -----------
        def _i(v, d, cap=None):
            try:
                x = int(v) if v is not None else d
                return min(x, cap) if cap else x
            except Exception:
                return d

        q = (request.query_params.get("q") or "").strip()
        course_id = request.query_params.get("course_id")
        subject_id = request.query_params.get("subject_id")
        teacher_id = request.query_params.get("teacher_id")
        only_active_courses = _i(request.query_params.get("only_active_courses"), 1)

        page = _i(request.query_params.get("page"), 1)
        page_size = _i(request.query_params.get("page_size"), 20, cap=100)

        # ----------- student enrollments -----------
        enroll_qs = Enrollment.objects.filter(student=student, status=Enrollment.Status.ACTIVE)
        if only_active_courses:
            enroll_qs = enroll_qs.filter(course__is_active=True)

        enrolled_course_ids = list(enroll_qs.values_list("course_id", flat=True))
        if not enrolled_course_ids:
            return Response({"count": 0, "page": page, "page_size": page_size, "results": []},
                            status=status.HTTP_200_OK)

        progress_map = {
            e.course_id: int(e.progress_pct or 0)
            for e in enroll_qs
        }

        # ----------- base modules queryset -----------
        modules_qs = (
            Module.objects
            .filter(active=True, course_id__in=enrolled_course_ids)
            .select_related("course", "course__subject", "course__classroom", "course__teacher__user")
            .annotate(
                lessons_count=Count("lessons", filter=Q(lessons__active=True), distinct=True),
                last_updated=Max("lessons__updated_at"),
            )
        )

        if course_id:
            modules_qs = modules_qs.filter(course_id=course_id)
        if subject_id:
            modules_qs = modules_qs.filter(course__subject_id=subject_id)
        if teacher_id:
            modules_qs = modules_qs.filter(course__teacher_id=teacher_id)
        if q:
            modules_qs = modules_qs.filter(
                Q(name__icontains=q) |
                Q(course__name__icontains=q) |
                Q(course__subject__name__icontains=q)
            )

        modules_qs = modules_qs.order_by("course_id", "order", "id")

        total = modules_qs.count()
        start = (page - 1) * page_size
        end = start + page_size
        modules_page = list(modules_qs[start:end])

        # ----------- fetch a recent/first active lesson per module -----------
        module_ids = [m.id for m in modules_page]
        latest_by_module: Dict[int, Lesson] = {}
        if module_ids:
            # first active lesson by (order, id)
            for ls in (Lesson.objects
                       .filter(active=True, module_id__in=module_ids)
                       .order_by("module_id", "order", "id")):
                if ls.module_id not in latest_by_module:
                    latest_by_module[ls.module_id] = ls

        # ----------- build response -----------
        results: List[Dict[str, Any]] = []
        for m in modules_page:
            c: Course = m.course
            teacher_user = getattr(c.teacher, "user", None) if c and c.teacher else None
            teacher_name = (teacher_user.get_full_name() or teacher_user.username) if teacher_user else None
            subj_name = getattr(c.subject, "name", None) if c and c.subject else None
            classroom_name = getattr(c.classroom, "name", None) if c and c.classroom else None

            rl = latest_by_module.get(m.id)
            recent = None
            if rl:
                # link to file or external url
                url = None
                if rl.file:
                    try:
                        url = rl.file.url
                    except Exception:
                        url = None
                if not url:
                    url = rl.url or None

                recent = {
                    "id": rl.id,
                    "title": rl.name,
                    "content_type": rl.content_type,
                    "duration_seconds": int(rl.duration_seconds or 0),
                    "url": url,
                }

            results.append({
                "id": m.id,
                "name": m.name,
                "order": m.order,
                "active": m.active,
                "course": {
                    "id": c.id if c else None,
                    "name": c.name if c else None,
                    "subject": subj_name,
                    "classroom": classroom_name,
                    "teacher": teacher_name,
                },
                "lessons_count": int(getattr(m, "lessons_count", 0) or 0),
                "last_updated": m.last_updated.isoformat() if getattr(m, "last_updated", None) else None,
                "course_progress": progress_map.get(c.id if c else None, 0),
                "recent_lesson": recent,
            })

        return Response(
            {"count": total, "page": page, "page_size": page_size, "results": results},
            status=status.HTTP_200_OK,
        )

    except Exception as e:
        err = {"detail": "Failed to load active modules for user.", "error": f"{type(e).__name__}: {e}"}
        if request.query_params.get("debug") in {"1", "true", "True"} or getattr(settings, "DEBUG", False):
            err["traceback"] = traceback.format_exc()
        return Response(err, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
