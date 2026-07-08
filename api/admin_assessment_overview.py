"""
admin_assessment_overview.py — Aggregated assessment overview endpoint for org admins.
Returns per-student scores across CBT tests, Code IDE projects, Assignment submissions,
and Off-Practical Work records for a given organization.
"""

import logging
from decimal import Decimal

from django.db.models import Avg, Count, Q
from django.core.paginator import Paginator

from rest_framework import status
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
)
from rest_framework.response import Response
from rest_framework_api_key.permissions import HasAPIKey

from academics.models import StudentProfile, Classroom
from api.authentication import SessionTokenAuthentication
from assessments.models import TestAttempt, Submission
from codeide.models import CodeProject
from core.utils import _resolve_org, _is_org_admin_or_teacher
from offline_work.models import OfflinePracticalScore

logger = logging.getLogger(__name__)


@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def assessment_overview(request):
    """
    GET /api/admin/assessment-overview/

    Query params:
      - classroom (int): filter by classroom id
      - search (str): filter students by name/email
      - page (int): default 1
      - page_size (int): default 20

    Returns per-student aggregated scores:
      - cbt_avg: average CBT test score as percentage of total_marks
      - cbt_count: number of submitted CBT attempts
      - code_avg: average Code IDE project score (out of 100)
      - code_count: number of graded Code IDE projects
      - assignment_avg: average Assignment submission score (out of 100)
      - assignment_count: number of graded Assignment submissions
      - opw_avg: average OPW score as percentage of max_score
      - opw_count: number of recorded OPW scores
      - overall_avg: weighted average across all categories
    """
    user = request.user
    org, msg = _resolve_org(request)
    if not org:
        return Response({"detail": msg or "Organization not found."}, status=400)

    # Must be admin or staff
    if not (user.is_staff or user.is_superuser or _is_org_admin_or_teacher(user, org)):
        return Response({"detail": "Admin access required."}, status=403)

    # Base student queryset for this org
    qs = StudentProfile.objects.filter(
        organization=org
    ).select_related("user", "current_classroom").order_by(
        "user__first_name", "user__last_name"
    )

    # Filters
    classroom_id = request.query_params.get("classroom")
    if classroom_id:
        qs = qs.filter(current_classroom_id=classroom_id)

    course_id = request.query_params.get("course")
    if course_id:
        # Filter students to only those enrolled in the course
        qs = qs.filter(enrollments__course_id=course_id, enrollments__status="active").distinct()

    search = request.query_params.get("search", "").strip()
    if search:
        qs = qs.filter(
            Q(user__first_name__icontains=search)
            | Q(user__last_name__icontains=search)
            | Q(user__email__icontains=search)
        )

    # Pagination
    page = max(1, int(request.query_params.get("page", 1)))
    page_size = min(50, max(1, int(request.query_params.get("page_size", 20))))
    paginator = Paginator(qs, page_size)
    page_obj = paginator.get_page(page)
    student_list = list(page_obj.object_list)
    student_ids = [s.id for s in student_list]

    # ── 1. CBT TestAttempt scores ───────────────────────────────────────────
    # score is absolute; total_marks is on the Test. We compute % per attempt.
    cbt_qs = TestAttempt.objects.filter(
        student_id__in=student_ids,
        status="submitted",
    )
    if course_id:
        cbt_qs = cbt_qs.filter(test__course_id=course_id)
        
    cbt_attempts = cbt_qs.select_related("test").values("student_id", "score", "test__total_marks")

    cbt_map: dict[int, list[float]] = {sid: [] for sid in student_ids}
    for a in cbt_attempts:
        sid = a["student_id"]
        total = float(a["test__total_marks"] or 100)
        if total > 0:
            pct = round(float(a["score"]) / total * 100, 2)
            cbt_map[sid].append(pct)

    # ── 2. Code IDE CodeProject scores ─────────────────────────────────────
    # CodeProject.score is 0–1000 (not 0–100), so convert to percentage
    CODE_MAX = 1000.0
    code_qs = CodeProject.objects.filter(
        student_id__in=student_ids,
        status=CodeProject.Status.GRADED,
        score__isnull=False,
    )
    if course_id:
        code_qs = code_qs.filter(lesson__module__course_id=course_id)
        
    code_projects = code_qs.values("student_id", "score")

    code_map: dict[int, list[float]] = {sid: [] for sid in student_ids}
    for p in code_projects:
        sid = p["student_id"]
        pct = round(float(p["score"]) / CODE_MAX * 100, 2)
        code_map[sid].append(pct)

    # ── 3. Assignment Submission scores ─────────────────────────────────────
    # score is out of 100 (no max on Assignment model — assumed 100)
    assign_qs = Submission.objects.filter(
        student_id__in=student_ids,
        score__isnull=False,
    )
    if course_id:
        assign_qs = assign_qs.filter(assignment__course_id=course_id)
        
    submissions = assign_qs.values("student_id", "score")

    assign_map: dict[int, list[float]] = {sid: [] for sid in student_ids}
    for s in submissions:
        sid = s["student_id"]
        assign_map[sid].append(float(s["score"]))

    # ── 4. OPW scores ───────────────────────────────────────────────────────
    opw_qs = OfflinePracticalScore.objects.filter(
        student_id__in=student_ids,
        score__isnull=False,
    )
    if course_id:
        opw_qs = opw_qs.filter(opw__course_id=course_id)
        
    opw_scores = opw_qs.select_related("opw").values("student_id", "score", "opw__max_score")

    opw_map: dict[int, list[float]] = {sid: [] for sid in student_ids}
    for o in opw_scores:
        sid = o["student_id"]
        max_s = float(o["opw__max_score"] or 100)
        if max_s > 0:
            pct = round(float(o["score"]) / max_s * 100, 2)
            opw_map[sid].append(pct)

    # ── Build per-student result ────────────────────────────────────────────
    def safe_avg(vals: list[float]) -> float | None:
        return round(sum(vals) / len(vals), 1) if vals else None

    def overall(cbt, code, assign, opw):
        """Weighted average — only categories with data contribute."""
        parts = [x for x in [cbt, code, assign, opw] if x is not None]
        return round(sum(parts) / len(parts), 1) if parts else None

    results = []
    for student in student_list:
        user = student.user
        name = f"{user.first_name} {user.last_name}".strip() or user.email
        sid = student.id

        cbt_avg = safe_avg(cbt_map[sid])
        code_avg = safe_avg(code_map[sid])
        assign_avg = safe_avg(assign_map[sid])
        opw_avg = safe_avg(opw_map[sid])
        overall_avg = overall(cbt_avg, code_avg, assign_avg, opw_avg)

        results.append({
            "student_id": sid,
            "student_name": name,
            "student_email": user.email,
            "classroom_id": student.current_classroom_id,
            "classroom_name": student.current_classroom.name if student.current_classroom else None,
            "cbt_avg": cbt_avg,
            "cbt_count": len(cbt_map[sid]),
            "code_avg": code_avg,
            "code_count": len(code_map[sid]),
            "assignment_avg": assign_avg,
            "assignment_count": len(assign_map[sid]),
            "opw_avg": opw_avg,
            "opw_count": len(opw_map[sid]),
            "overall_avg": overall_avg,
        })

    # ── Platform-wide summary ───────────────────────────────────────────────
    all_overall = [r["overall_avg"] for r in results if r["overall_avg"] is not None]
    platform_avg = round(sum(all_overall) / len(all_overall), 1) if all_overall else None

    return Response({
        "results": results,
        "summary": {
            "platform_avg": platform_avg,
            "total_students": paginator.count,
        },
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": paginator.count,
            "pages": paginator.num_pages,
        },
    })


@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def assessment_classrooms(request):
    """
    GET /api/admin/assessment-overview/classrooms/
    Returns classrooms for the org (for the filter dropdown).
    """
    user = request.user
    org, msg = _resolve_org(request)
    if not org:
        return Response({"detail": msg or "Organization not found."}, status=400)

    if not (user.is_staff or user.is_superuser or _is_org_admin_or_teacher(user, org)):
        return Response({"detail": "Admin access required."}, status=403)

    classrooms = Classroom.objects.filter(organization=org).order_by("name")
    data = [{"id": c.id, "name": c.name} for c in classrooms]
    return Response(data)

@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def assessment_courses(request):
    """
    GET /api/admin/assessment-overview/courses/
    Returns courses for the org (for the filter dropdown).
    """
    from learning.models import Course
    user = request.user
    org, msg = _resolve_org(request)
    if not org:
        return Response({"detail": msg or "Organization not found."}, status=400)

    if not (user.is_staff or user.is_superuser or _is_org_admin_or_teacher(user, org)):
        return Response({"detail": "Admin access required."}, status=403)

    courses = Course.objects.filter(organization=org).order_by("name")
    data = [{"id": c.id, "name": c.name} for c in courses]
    return Response(data)
