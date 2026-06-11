# api/views.py
from typing import Any, Dict, List, Optional, Tuple
import traceback
from datetime import timedelta

from django.conf import settings
from django.db.models import Q, Sum, Count, Value, IntegerField
from django.utils import timezone
from django.http import JsonResponse
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.response import Response
from rest_framework_api_key.permissions import HasAPIKey

from api.authentication import SessionTokenAuthentication
from academics.models import ParentProfile, ParentChildLink, StudentProfile
from assessments.models import TestAttempt
from codeide.models import CodeProject  # noqa: F401
from django.db.models.functions import Coalesce
from gamification.models import (
    Badge,
    BadgeAward,
    PointTransaction,
    Streak,
    AchievementDefinition,
    AchievementAcquired,
    ActivityEvent,
    LeaderboardSeason,
)
from learning.models import Enrollment
from orgs.models import OrganizationMembership,Organization
from core.utils import (
    _sum_points,
    _resolve_org,
    _status_from_user_membership,
    _avatar_url_for,
    resolve_season,
    _get_admin_selected_org_id
)
from api.permissions import RequiresActiveStudentSubscription
from core.permissions import IsAdminAccess 
from gamification.services.rules import SUPPORTED_METRICS
from .serializers import AchievementDefinitionSerializer, BadgeSerializer
from django.db import transaction


@api_view(["GET"])
@permission_classes([HasAPIKey, IsAdminAccess])
@authentication_classes([SessionTokenAuthentication])
def admin_student_leaderboard(request):
    """
    Admin leaderboard:
    - filter by season
    - search by student name
    - scope by selected org or all orgs
    """
    try:
        org_id = _get_admin_selected_org_id(request)
        if not org_id:
            return Response({"detail": "No selected organization."}, status=status.HTTP_400_BAD_REQUEST)

        # query params
        season_id = request.query_params.get("season")
        q = (request.query_params.get("q") or "").strip()
        org_scope = (request.query_params.get("org_scope") or "selected").strip()  # selected | all
        page = int(request.query_params.get("page") or 1)
        page_size = min(int(request.query_params.get("page_size") or 25), 100)

        # base student qs
        students = StudentProfile.objects.select_related("user", "organization")

        if org_scope != "all":
            students = students.filter(organization_id=org_id)

        if q:
            # adjust fields to match your User model fields
            students = students.filter(
                Q(user__first_name__icontains=q) |
                Q(user__last_name__icontains=q) |
                Q(user__email__icontains=q)
            )

        # season filter for transactions/badges/achievements
        season = None
        if season_id:
            season = LeaderboardSeason.objects.filter(id=season_id).first()

        tx_filter = Q(point_transactions__isnull=False)
        if season:
            tx_filter &= Q(point_transactions__season=season)
        else:
            # if no season passed, you can default to active for selected org
            # (or return error; your choice)
            active = LeaderboardSeason.get_active(Organization.objects.filter(id=org_id).first())
            if active:
                season = active
                tx_filter &= Q(point_transactions__season=active)

        # annotate totals
        students = students.annotate(
            total_points=Coalesce(
                Sum("point_transactions__points", filter=tx_filter),
                Value(0),
                output_field=IntegerField(),
            ),
            badges_count=Coalesce(
                Count("badge_awards", filter=Q(badge_awards__season=season) if season else Q()),
                Value(0),
                output_field=IntegerField(),
            ),
            achievements_count=Coalesce(
                Count("achievements_acquired", filter=Q(achievements_acquired__season=season) if season else Q()),
                Value(0),
                output_field=IntegerField(),
            ),
        ).order_by("-total_points", "user__last_name", "user__first_name")

        total = students.count()

        # pagination
        start = (page - 1) * page_size
        end = start + page_size
        page_qs = students[start:end]

        # compute rank (simple rank = offset + index + 1)
        results = []
        for idx, s in enumerate(page_qs, start=start + 1):
            results.append({
                "rank": idx,
                "student_id": s.id,
                "name": s.user.get_full_name() or s.user.username,
                "organization": s.organization.name if s.organization else None,
                "total_points": int(s.total_points or 0),
                "badges_count": int(s.badges_count or 0),
                "achievements_count": int(s.achievements_count or 0),
            })

        return Response({
            "season": {"id": season.id, "name": season.name} if season else None,
            "org_scope": org_scope,
            "page": page,
            "page_size": page_size,
            "total": total,
            "results": results,
        })
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.exception("admin_student_leaderboard failed")
        return JsonResponse({"error": str(e)}, status=500)



@api_view(["GET"])
@permission_classes([HasAPIKey, IsAdminAccess])
@authentication_classes([SessionTokenAuthentication])
def admin_leaderboard_seasons(request):
    org_id = _get_admin_selected_org_id(request)
    if not org_id:
        return Response({"detail": "No selected organization."}, status=status.HTTP_400_BAD_REQUEST)

    org_scope = (request.query_params.get("org_scope") or "selected").strip()

    qs = LeaderboardSeason.objects.all()
    #if org_scope != "all":
        #qs = qs.filter(organization_id=org_id)

    qs = qs.order_by("-start_at")

    return Response([
        {
            "id": s.id,
            "name": s.name,
            "slug": s.slug,
            "start_at": s.start_at,
            "end_at": s.end_at,
            "is_active": s.is_active,
            "organization_id": s.organization_id,
        }
        for s in qs
    ])

@api_view(["GET"])
@permission_classes([HasAPIKey, IsAdminAccess])
@authentication_classes([SessionTokenAuthentication])
def admin_gamification_meta(request):
    org_id = _get_admin_selected_org_id(request)
    if not org_id:
        return Response({"detail": "No selected organization."}, status=status.HTTP_400_BAD_REQUEST)

    # “what available”: event types that actually exist for this org
    event_types = list(
        ActivityEvent.objects.filter(organization_id=org_id)
        .values_list("event_type", flat=True)
        .distinct()
        .order_by("event_type")
    )

    return Response(
        {
            "supported_metrics": sorted(list(SUPPORTED_METRICS)),
            "available_event_types": event_types,
            # "seed_templates": DEFAULT_ACHIEVEMENTS,  # optional
        },
        status=status.HTTP_200_OK,
    )


# -------- AchievementDefinition --------

@api_view(["GET", "POST"])
@permission_classes([HasAPIKey, IsAdminAccess])
@authentication_classes([SessionTokenAuthentication])
@transaction.atomic
def admin_achievement_definitions(request):
    org_id = _get_admin_selected_org_id(request)
    if not org_id:
        return Response({"detail": "No selected organization."}, status=status.HTTP_400_BAD_REQUEST)

    if request.method == "GET":
        qs = AchievementDefinition.objects.all().order_by("code")
        # optional filters
        q = (request.query_params.get("q") or "").strip()
        active = request.query_params.get("active")
        if q:
            qs = qs.filter(
                Q(code__icontains=q) | Q(title__icontains=q) | Q(category__icontains=q)
            )
        if active in ("true", "false"):
            qs = qs.filter(is_active=(active == "true"))

        return Response(AchievementDefinitionSerializer(qs, many=True).data)

    # POST create
    serializer = AchievementDefinitionSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    # attach org
    if request.user.adminaccess.super_user:
        obj = AchievementDefinition.objects.create(
            **serializer.validated_data,
        )
        return Response(AchievementDefinitionSerializer(obj).data, status=status.HTTP_201_CREATED)
    return Response({"detail":"Url not available"}, status=status.HTTP_404_NOT_FOUND)


@api_view(["PATCH"])
@permission_classes([HasAPIKey, IsAdminAccess])
@authentication_classes([SessionTokenAuthentication])
@transaction.atomic
def admin_achievement_definition_update(request, pk: int):
    org_id = _get_admin_selected_org_id(request)
    if not org_id:
        return Response({"detail": "No selected organization."}, status=status.HTTP_400_BAD_REQUEST)

    obj = AchievementDefinition.objects.filter(id=pk).first()
    if not obj:
        return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

    serializer = AchievementDefinitionSerializer(obj, data=request.data, partial=True)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    if request.user.adminaccess.super_user:
        serializer.save()
    return Response(AchievementDefinitionSerializer(obj).data, status=status.HTTP_200_OK)


@api_view(["POST"])
@permission_classes([HasAPIKey, IsAdminAccess])
@authentication_classes([SessionTokenAuthentication])
@transaction.atomic
def admin_achievement_definition_status_update(request, pk: int):
    org_id = _get_admin_selected_org_id(request)
    if not org_id:
        return Response(
            {"detail": "No selected organization."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    obj = AchievementDefinition.objects.filter(id=pk).first()
    if not obj:
        return Response(
            {"detail": "Not found."},
            status=status.HTTP_404_NOT_FOUND,
        )

    # Toggle active/inactive
    obj.is_active = not obj.is_active
    if request.user.adminaccess.super_user:
        obj.save(update_fields=["is_active"])

    return Response(
        {
            "detail": "Activated." if obj.is_active else "Deactivated.",
            "is_active": obj.is_active,
        },
        status=status.HTTP_200_OK,
    )

# -------- Badge --------

@api_view(["GET", "POST"])
@permission_classes([HasAPIKey, IsAdminAccess])
@authentication_classes([SessionTokenAuthentication])
@transaction.atomic
def admin_badges(request):
    org_id = _get_admin_selected_org_id(request)
    if not org_id:
        return Response({"detail": "No selected organization."}, status=status.HTTP_400_BAD_REQUEST)
    if request.method == "GET":
        qs = Badge.objects.all().order_by("points", "name")
        q = (request.query_params.get("q") or "").strip()
        active = request.query_params.get("active")
        if q:
            qs = qs.filter(Q(name__icontains=q))
        if active == "true":
            qs = qs.filter(is_active=True)
        elif active == "false":
            qs = qs.filter(is_active=False)

        return Response(BadgeSerializer(qs, many=True).data)

    serializer = BadgeSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    if request.user.adminaccess.super_user:
        obj = Badge.objects.create(
            **serializer.validated_data,
        )
        return Response(BadgeSerializer(obj).data, status=status.HTTP_201_CREATED)
    return Response({"detail":"Url not available"}, status=status.HTTP_404_NOT_FOUND)


@api_view(["PATCH"])
@permission_classes([HasAPIKey, IsAdminAccess])
@authentication_classes([SessionTokenAuthentication])
@transaction.atomic
def admin_badge_update(request, pk: int):
    org_id = _get_admin_selected_org_id(request)
    if not org_id:
        return Response({"detail": "No selected organization."}, status=status.HTTP_400_BAD_REQUEST)

    obj = Badge.objects.filter(id=pk).first()
    if not obj:
        return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

    serializer = BadgeSerializer(obj, data=request.data, partial=True)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
    if request.user.adminaccess.super_user:
        serializer.save()
    return Response(BadgeSerializer(obj).data, status=status.HTTP_200_OK)

@api_view(["POST"])
@permission_classes([HasAPIKey, IsAdminAccess])
@authentication_classes([SessionTokenAuthentication])
@transaction.atomic
def admin_badge_status_update(request, pk: int):
    org_id = _get_admin_selected_org_id(request)
    if not org_id:
        return Response(
            {"detail": "No selected organization."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    obj = Badge.objects.filter(id=pk).first()
    if not obj:
        return Response(
            {"detail": "Not found."},
            status=status.HTTP_404_NOT_FOUND,
        )

    # Toggle active/inactive
    obj.is_active = not obj.is_active
    if request.user.adminaccess.super_user:
        obj.save(update_fields=["is_active"])

    return Response(
        {
            "detail": "Activated." if obj.is_active else "Deactivated.",
            "is_active": obj.is_active,
        },
        status=status.HTTP_200_OK,
    )

# ---------- helpers ----------
def _get_student_for_user(user) -> Optional[StudentProfile]:
    mem = (OrganizationMembership.objects
           .filter(user=user, is_active=True)
           .select_related("organization")
           .order_by("-id")
           .first())
    if mem:
        sp = StudentProfile.objects.filter(user=user, organization=mem.organization)\
                                   .select_related("user", "organization").first()
        if sp:
            return sp
    return (StudentProfile.objects
            .filter(user=user).select_related("user", "organization")
            .order_by("-id").first())


def _avatar_url(u) -> Optional[str]:
    try:
        if getattr(u, "avatar", None) and getattr(u.avatar, "url", None):
            return u.avatar.url
    except Exception:
        pass
    return None


def _name_from_user(u) -> str:
    try:
        full = u.get_full_name()
        return full if full else u.username
    except Exception:
        return "Student"


def _top_rows(qs, top: int) -> List[Dict[str, Any]]:
    # qs is a values/annotate queryset with fields: student_id, xp
    top = max(int(top or 0), 0)
    if top == 0:
        return []
    return list(qs.order_by("-xp", "student_id")[:top])


def _badge_count_map(student_ids: List[int], season=None) -> Dict[int, int]:
    """
    Returns {student_id: badge_award_count}.

    If season is provided, counts only awards in that season
    (aligns with the season-filtered leaderboard endpoint).
    """
    if not student_ids:
        return {}

    qs = BadgeAward.objects.filter(student_id__in=student_ids)
    if season is not None:
        qs = qs.filter(season=season)

    rows = qs.values("student_id").annotate(cnt=Count("id"))
    return {int(r["student_id"]): int(r["cnt"] or 0) for r in rows}


def _streak_map(student_ids: List[int], season=None) -> Dict[int, int]:
    """
    Returns {student_id: current_streak_days}.

    If season is provided, reads streaks for that season.
    If no season is provided, reads any streak row (typically "all-time" behavior).
    """
    if not student_ids:
        return {}

    qs = Streak.objects.filter(student_id__in=student_ids)
    if season is not None:
        qs = qs.filter(season=season)

    rows = qs.values("student_id", "current_days")
    return {int(r["student_id"]): int(r["current_days"] or 0) for r in rows}


def _profiles_map(student_ids: List[int]) -> Dict[int, "StudentProfile"]:
    if not student_ids:
        return {}
    profs = (
        StudentProfile.objects.filter(id__in=student_ids)
        .select_related("user", "organization")
    )
    return {int(p.id): p for p in profs}


def _rank_for_student(all_qs, student_id: int, student_points: int) -> Optional[int]:
    """
    Compute rank = 1 + count(students with xp > student_points).

    IMPORTANT: all_qs MUST already be season-filtered (and org-filtered where applicable)
    to match the endpoint.
    Example:
      all_qs = PointTransaction.objects.filter(season=season).values("student_id").annotate(xp=Sum("points"))
    """
    if student_id is None:
        return None
    try:
        # ensure int to avoid weird comparisons
        student_points = int(student_points or 0)
        higher = all_qs.filter(xp__gt=student_points).count()
        return int(higher) + 1
    except Exception:
        return None

# ---------- endpoint ----------
@api_view(["GET"])
@permission_classes([HasAPIKey, RequiresActiveStudentSubscription()])
@authentication_classes([SessionTokenAuthentication])
def leaderboard_overview(request):
    """
    Data for the Leaderboard UI (leaderboard.tsx).

    Seasonal behavior:
      - Resolves the season using resolve_season(org, now)
      - Filters ALL point-based leaderboards/stats by that season
      - (Optionally) filters badges/streaks by season if your helpers support it

    Response:
      {
        "season": {id, name, slug, start_at, end_at, is_active} | null,
        "stats": {
          "global_rank": int|None,
          "school_rank": int|None,
          "total_points": int,
          "weekly_points": int,
          "competitors": int
        },
        "global": [ {rank, name, school, points, avatar, streak, badges, isCurrentUser?}, ... ],
        "school": [ {rank, name, points, avatar, streak, badges, isCurrentUser?}, ... ],
        "weekly": [ {rank, name, points, avatar, streak, isCurrentUser?}, ... ]
      }

    Query params (optional):
      - top_global (default 10)
      - top_school (default 10)
      - top_weekly (default 10)
      - debug=1 to include traceback on error
    """
    try:
        user = request.user
        student = _get_student_for_user(user)
        if not student:
            return Response({
                "season": None,
                "stats": {
                    "global_rank": None,
                    "school_rank": None,
                    "total_points": 0,
                    "weekly_points": 0,
                    "competitors": 0,
                },
                "global": [],
                "school": [],
                "weekly": [],
            }, status=status.HTTP_200_OK)


        org = student.organization
        now = timezone.now()
        week_since = now - timedelta(days=7)
        month_since = now - timedelta(days=30)

        # ---- resolve current season ----
        # Uses your helper:
        #   - prefers active season if it contains now
        #   - else finds by date range
        season = resolve_season(org, now)
        def _season_filter(qs, season_obj):
            """If season exists, restrict to that season; else leave queryset unchanged (all-time)."""
            return qs.filter(season=season_obj) if season_obj else qs

        def _i(s, d):  # safe int
            try:
                return int(s) if s is not None else d
            except Exception:
                return d

        top_global = _i(request.query_params.get("top_global"), 10)
        top_school = _i(request.query_params.get("top_school"), 10)
        top_weekly = _i(request.query_params.get("top_weekly"), 10)

        # ---- TOTAL points for current student (seasonal) ----
        student_total_points = int(
            _season_filter(PointTransaction.objects.filter(student=student), season)
            .aggregate(x=Sum("points"))
            .get("x") or 0
        )
        # ---- GLOBAL leaderboard (all orgs, season-filtered) ----
        global_base = _season_filter(PointTransaction.objects.all(), season)
        global_all = global_base.values("student_id").annotate(xp=Sum("points"))

        global_top = _top_rows(global_all, top_global)
        global_ids = [r["student_id"] for r in global_top]

        if student.id not in global_ids:
            global_ids.append(student.id)

        # If your helpers accept season, pass it.
        # If they don't, remove season=season from these calls.
        global_badges = _badge_count_map(global_ids, season=season)
        global_streaks = _streak_map(global_ids, season=season)
        global_profiles = _profiles_map(global_ids)

        global_list: List[Dict[str, Any]] = []
        rank_counter = 1
        seen = set()

        for row in sorted(global_top, key=lambda r: (-int(r["xp"] or 0), r["student_id"])):
            sid = row["student_id"]
            seen.add(sid)
            prof = global_profiles.get(sid)
            if not prof:
                continue

            global_list.append({
                "rank": rank_counter,
                "name": _name_from_user(prof.user),
                "school": getattr(prof.organization, "name", "") or "School",
                "points": int(row["xp"] or 0),
                "avatar": _avatar_url(prof.user),
                "streak": global_streaks.get(sid, 0),
                "badges": global_badges.get(sid, 0),
                "isCurrentUser": bool(sid == student.id),
            })
            rank_counter += 1
        if student.id not in seen:
            prof = global_profiles.get(student.id)
            if prof:
                global_rank = _rank_for_student(global_all, student.id, student_total_points)
                global_list.append({
                    "rank": global_rank or None,
                    "name": _name_from_user(prof.user),
                    "school": getattr(prof.organization, "name", "") or "School",
                    "points": student_total_points,
                    "avatar": _avatar_url(prof.user),
                    "streak": global_streaks.get(student.id, 0),
                    "badges": global_badges.get(student.id, 0),
                    "isCurrentUser": True,
                })

        # ---- SCHOOL leaderboard (within same org, season-filtered) ----
        school_base = PointTransaction.objects.filter(student__organization=org)
        school_base = _season_filter(school_base, season)
        school_all = school_base.values("student_id").annotate(xp=Sum("points"))

        school_top = _top_rows(school_all, top_school)
        school_ids = [r["student_id"] for r in school_top]
        if student.id not in school_ids:
            school_ids.append(student.id)

        school_badges = _badge_count_map(school_ids, season=season)
        school_streaks = _streak_map(school_ids, season=season)
        school_profiles = _profiles_map(school_ids)

        school_list: List[Dict[str, Any]] = []
        rank_counter = 1
        seen = set()

        for row in sorted(school_top, key=lambda r: (-int(r["xp"] or 0), r["student_id"])):
            sid = row["student_id"]
            seen.add(sid)
            prof = school_profiles.get(sid)
            if not prof:
                continue

            school_list.append({
                "rank": rank_counter,
                "name": _name_from_user(prof.user),
                "points": int(row["xp"] or 0),
                "avatar": _avatar_url(prof.user),
                "streak": school_streaks.get(sid, 0),
                "badges": school_badges.get(sid, 0),
                "isCurrentUser": bool(sid == student.id),
            })
            rank_counter += 1

        if student.id not in seen:
            prof = school_profiles.get(student.id)
            if prof:
                school_rank = _rank_for_student(school_all, student.id, student_total_points)
                school_list.append({
                    "rank": school_rank or None,
                    "name": _name_from_user(prof.user),
                    "points": student_total_points,
                    "avatar": _avatar_url(prof.user),
                    "streak": school_streaks.get(student.id, 0),
                    "badges": school_badges.get(student.id, 0),
                    "isCurrentUser": True,
                })

        # ---- WEEKLY leaderboard (last 7 days, within org, season-filtered) ----
        weekly_base = PointTransaction.objects.filter(
            student__organization=org,
            created_at__gte=week_since,
        )
        weekly_base = _season_filter(weekly_base, season)
        weekly_all = weekly_base.values("student_id").annotate(xp=Sum("points"))

        weekly_top = _top_rows(weekly_all, top_weekly)
        weekly_ids = [r["student_id"] for r in weekly_top]
        if student.id not in weekly_ids:
            weekly_ids.append(student.id)

        weekly_profiles = _profiles_map(weekly_ids)
        weekly_streaks = _streak_map(weekly_ids, season=season)

        weekly_list: List[Dict[str, Any]] = []
        rank_counter = 1
        seen = set()

        for row in sorted(weekly_top, key=lambda r: (-int(r["xp"] or 0), r["student_id"])):
            sid = row["student_id"]
            seen.add(sid)
            prof = weekly_profiles.get(sid)
            if not prof:
                continue

            weekly_list.append({
                "rank": rank_counter,
                "name": _name_from_user(prof.user),
                "points": int(row["xp"] or 0),
                "avatar": _avatar_url(prof.user),
                "streak": weekly_streaks.get(sid, 0),
                "isCurrentUser": bool(sid == student.id),
            })
            rank_counter += 1

        if student.id not in seen:
            prof = weekly_profiles.get(student.id)
            if prof:
                my_week_pts = int(
                    _season_filter(
                        PointTransaction.objects.filter(student=student, created_at__gte=week_since),
                        season,
                    ).aggregate(x=Sum("points")).get("x") or 0
                )
                my_week_rank = _rank_for_student(weekly_all, student.id, my_week_pts)
                weekly_list.append({
                    "rank": my_week_rank or None,
                    "name": _name_from_user(prof.user),
                    "points": my_week_pts,
                    "avatar": _avatar_url(prof.user),
                    "streak": weekly_streaks.get(student.id, 0),
                    "isCurrentUser": True,
                })

        # ---- stats header (season-filtered ranks) ----
        global_rank = _rank_for_student(global_all, student.id, student_total_points)
        school_rank = _rank_for_student(school_all, student.id, student_total_points)
        weekly_points = next((i["points"] for i in weekly_list if i.get("isCurrentUser")), 0)

        # competitors: active learners in school in last 30 days (season-filtered)
        competitors_qs = PointTransaction.objects.filter(
            student__organization=org,
            created_at__gte=month_since,
        )
        competitors_qs = _season_filter(competitors_qs, season)

        competitors = competitors_qs.values("student_id").distinct().count()
        if competitors == 0:
            competitors = StudentProfile.objects.filter(organization=org).count() or 0

        # ---- if everything is empty, provide nice dummies ----
        if not global_list and not school_list and not weekly_list:
            return Response({
                "season": None,
                "stats": {
                    "global_rank": None,
                    "school_rank": None,
                    "total_points": 0,
                    "weekly_points": 0,
                    "competitors": 0,
                },
                "global": [],
                "school": [],
                "weekly": [],
            }, status=status.HTTP_200_OK)


        payload = {
            "season": None if not season else {
                "id": season.id,
                "name": season.name,
                "slug": season.slug,
                "start_at": season.start_at,
                "end_at": season.end_at,
                "is_active": season.is_active,
            },
            "stats": {
                "global_rank": global_rank,
                "school_rank": school_rank,
                "total_points": student_total_points,
                "weekly_points": weekly_points,
                "competitors": competitors,
            },
            "global": global_list,
            "school": school_list,
            "weekly": weekly_list,
        }
        return Response(payload, status=status.HTTP_200_OK)

    except Exception as e:
        print(e)
        err = {"detail": "Failed to load leaderboard.", "error": f"{type(e).__name__}: {e}"}
        if request.query_params.get("debug") in {"1", "true", "True"} or getattr(settings, "DEBUG", False):
            err["traceback"] = traceback.format_exc()
        return Response(err, status=status.HTTP_500_INTERNAL_SERVER_ERROR)




def _parent_dummy() -> Dict[str, Any]:
    return {
        "children": [],
        "leaderboard": [],
    }


@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def parent_rewards(request):

    """
    Endpoint for the parent rewards page.
    Returns data for children rewards tracking and leaderboard.

    Query params:
      - page_leaderboard (default 1)
      - limit_leaderboard (default 5, max 20)
      - debug=1 to include traceback in error responses

    Response:
      {
        "children": [...],          # list of child dicts with points, streak, badges, etc.
        "leaderboard": [...],       # paginated list of top students across all organizations
      }
    """
    try:
        user = request.user
        parent = ParentProfile.objects.filter(user=user).first()
        if not parent:
            return Response(_parent_dummy(), status=status.HTTP_200_OK)

        # Get children — optionally filtered by child_id query param
        child_id_param = request.query_params.get("child_id")
        children_links = ParentChildLink.objects.filter(parent=parent)
        if child_id_param and child_id_param != "all":
            try:
                children_links = children_links.filter(student__id=int(child_id_param))
            except (ValueError, TypeError):
                pass
        student_ids = list(children_links.values_list("student_id", flat=True))
        students = StudentProfile.objects.filter(id__in=student_ids).select_related("user", "organization")

        # Pagination params
        def _int_param(key, default, max_val=None):
            try:
                val = int(request.query_params.get(key, default))
                return min(val, max_val) if max_val else val
            except Exception:
                return default

        page_leaderboard = max(1, _int_param("page_leaderboard", 1))
        limit_leaderboard = _int_param("limit_leaderboard", 5, 20)

        # ---------- Children data ----------
        children_data: List[Dict[str, Any]] = []

        # inside parent_rewards()

        for student in students:
            org = getattr(student, "organization", None)
            total_points = _sum_points(student)
            streak_obj = Streak.objects.filter(student=student).order_by("-last_activity").first()
            current_streak = streak_obj.current_days if streak_obj else 0

            # -----------------------
            # Badges (Badge + BadgeAward)
            # -----------------------
            all_badges = Badge.objects.filter(Q(organization__isnull=True) | Q(organization=org)).order_by("points", "id")
            awarded = (
                BadgeAward.objects
                .filter(student=student, badge__in=all_badges)
                .select_related("badge")
            )
            awarded_map = {aw.badge_id: aw for aw in awarded}

            badges_data = []
            for b in all_badges:
                aw = awarded_map.get(b.id)
                badges_data.append({
                    "id": b.id,
                    "name": b.name,
                    "icon": b.icon_name or "medal",
                    "color": b.color or "bg-gray-400",
                    "pointsThreshold": b.points or 0,
                    "earned": bool(aw),
                    "earnedAt": aw.awarded_at.strftime("%Y-%m-%d") if aw else None,
                    "reason": aw.reason if aw else "",
                })

            # -----------------------
            # Achievements (AchievementAcquired + AchievementDefinition)
            # -----------------------
            achievement_defs = AchievementDefinition.objects.filter(
                Q(organization__isnull=True) | Q(organization=org),
                is_active=True,
            )

            acquired = (
                AchievementAcquired.objects
                .filter(student=student, definition__in=achievement_defs)
                .select_related("definition")
                .order_by("-acquired_at")[:5]
            )

            achievements_data = [{
                "code": a.definition.code,
                "title": a.definition.title,
                "description": a.definition.description,
                "icon": a.definition.icon,
                "category": a.definition.category,
                "points": a.definition.points,
                "acquiredAt": a.acquired_at.strftime("%Y-%m-%d"),
                "valueAtUnlock": a.value_at_unlock,
            } for a in acquired]

            # -----------------------
            # Recent Points (PointTransaction ledger)
            # -----------------------
            recent_trans = PointTransaction.objects.filter(student=student).order_by("-created_at")[:5]
            recent_points = [{
                "reason": trans.reason or "Points Earned",
                "points": trans.points,
                "date": trans.created_at.strftime("%Y-%m-%d"),
                "balanceAfter": trans.balance_after,
            } for trans in recent_trans]

            avatar = _avatar_url_for(student.user, request) or "/placeholder.svg?height=40&width=40"

            children_data.append({
                "id": student.id,
                "name": student.user.get_full_name() or student.user.username,
                "avatar": avatar,
                "totalPoints": total_points,
                "currentStreak": current_streak,
                "badges": badges_data,
                "achievements": achievements_data,
                "recentPoints": recent_points,
            })


        # ---------- Leaderboard (top by points, season-filtered) ----------
        org = getattr(students.first(), "organization", None) if students.exists() else None
        season = resolve_season(org, timezone.now()) if org else None
        points_qs = PointTransaction.objects.values("student_id")
        if season:
            points_qs = points_qs.filter(season=season)
        points_agg = points_qs.annotate(total=Sum("points")).order_by("-total")
        top_points = list(points_agg[:limit_leaderboard * page_leaderboard])
        start = (page_leaderboard - 1) * limit_leaderboard
        paginated_points = top_points[start : start + limit_leaderboard]

        all_student_ids = [p["student_id"] for p in paginated_points]
        all_students = StudentProfile.objects.filter(id__in=all_student_ids).select_related("user", "organization")
        student_map = {s.id: s for s in all_students}

        leaderboard = []
        base_rank = start + 1
        for idx, p in enumerate(paginated_points):
            s = student_map.get(p["student_id"])
            if not s:
                continue
            name = s.user.get_full_name() or s.user.username
            is_child = p["student_id"] in student_ids
            leaderboard.append({
                "rank": base_rank + idx,
                "name": name,
                "school": getattr(s.organization, "name", "") or "School",
                "points": p["total"] or 0,
                "isChild": is_child
            })

        return Response({
            "children": children_data,
            "leaderboard": leaderboard,
        }, status=status.HTTP_200_OK)

    except Exception as e:
        err = {"detail": "Failed to load rewards data.", "error": f"{type(e).__name__}: {e}"}
        if request.query_params.get("debug") in {"1", "true", "True"} or getattr(settings, "DEBUG", False):
            err["traceback"] = traceback.format_exc()
        return Response(err, status=status.HTTP_500_INTERNAL_SERVER_ERROR)








        