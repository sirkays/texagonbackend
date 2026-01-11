# api/views.py
from typing import Any, Dict, List, Optional, Tuple
import traceback
from datetime import timedelta

from django.conf import settings
from django.db.models import Q, Sum, Count
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.response import Response
from rest_framework_api_key.permissions import HasAPIKey

from api.authentication import SessionTokenAuthentication
from academics.models import ParentProfile, ParentChildLink, StudentProfile
from assessments.models import TestAttempt
from codeide.models import CodeSubmission
from gamification.models import (
    Badge,
    BadgeAward,
    PointTransaction,
    Streak,
    AchievementDefinition,
    AchievementAcquired,
)
from learning.models import Enrollment
from orgs.models import OrganizationMembership
from core.utils import (
    _sum_points,
    _resolve_org,
    _status_from_user_membership,
    _avatar_url_for,
    resolve_season,
)
from api.permissions import RequiresActiveStudentSubscription
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
from typing import Any, Dict, List, Optional

from django.db.models import Count
# from academics.models import StudentProfile
# from achievements.models import BadgeAward, Streak


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
@permission_classes([HasAPIKey, RequiresActiveStudentSubscription])
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

        # Get children
        children_links = ParentChildLink.objects.filter(parent=parent)
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
            streak_obj = Streak.objects.filter(student=student).first()
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


        # ---------- Leaderboard (top by points globally) ----------
        points_agg = PointTransaction.objects.values("student_id").annotate(total=Sum("points")).order_by("-total")
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








        