# api/views.py
from typing import Any, Dict, List, Optional, Tuple
import traceback
from datetime import timedelta

from django.conf import settings
from django.db.models import Sum, Count, Q
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.response import Response
from rest_framework import status
from rest_framework_api_key.permissions import HasAPIKey

from api.authentication import SessionTokenAuthentication

# 🔧 adjust imports to match your app layout
from orgs.models import OrganizationMembership
from academics.models import StudentProfile
from gamification.models import PointTransaction, BadgeAward, Streak


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
    # qs is a values/annotate set with fields: student_id, xp
    return list(qs.order_by("-xp", "student_id")[:top])


def _badge_count_map(student_ids: List[int]) -> Dict[int, int]:
    if not student_ids:
        return {}
    rows = (BadgeAward.objects.filter(student_id__in=student_ids)
            .values("student_id")
            .annotate(cnt=Count("id")))
    return {r["student_id"]: int(r["cnt"]) for r in rows}


def _streak_map(student_ids: List[int]) -> Dict[int, int]:
    if not student_ids:
        return {}
    rows = Streak.objects.filter(student_id__in=student_ids)\
                         .values("student_id", "current_days")
    return {r["student_id"]: int(r["current_days"] or 0) for r in rows}


def _profiles_map(student_ids: List[int]) -> Dict[int, StudentProfile]:
    if not student_ids:
        return {}
    profs = (StudentProfile.objects.filter(id__in=student_ids)
             .select_related("user", "organization"))
    return {p.id: p for p in profs}


def _rank_for_student(all_qs, student_id: int, student_points: int) -> Optional[int]:
    """
    Compute rank = 1 + count(students with xp > student_points).
    all_qs must be: PointTransaction.objects.values("student_id").annotate(xp=Sum("points"))
    """
    if student_id is None:
        return None
    try:
        higher = all_qs.filter(xp__gt=student_points).count()
        return int(higher) + 1
    except Exception:
        return None


# ---------- endpoint ----------
@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def leaderboard_overview(request):
    """
    Data for the Leaderboard UI (leaderboard.tsx).

    Response:
      {
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
            # No student context → return full dummy payload that matches the UI
            return Response(_dummy_payload(), status=status.HTTP_200_OK)

        org = student.organization
        now = timezone.now()
        week_since = now - timedelta(days=7)

        def _i(s, d):  # safe int
            try:
                return int(s) if s is not None else d
            except Exception:
                return d

        top_global = _i(request.query_params.get("top_global"), 10)
        top_school = _i(request.query_params.get("top_school"), 10)
        top_weekly = _i(request.query_params.get("top_weekly"), 10)

        # ---- TOTAL points for current student ----
        student_total_points = int(
            PointTransaction.objects.filter(student=student)
            .aggregate(x=Sum("points")).get("x") or 0
        )

        # ---- GLOBAL leaderboard (all orgs) ----
        global_all = (PointTransaction.objects
                      .values("student_id")
                      .annotate(xp=Sum("points")))
        global_top = _top_rows(global_all, top_global)
        global_ids = [r["student_id"] for r in global_top]

        # include current student if not present
        if student.id not in global_ids:
            global_ids.append(student.id)

        global_badges = _badge_count_map(global_ids)
        global_streaks = _streak_map(global_ids)
        global_profiles = _profiles_map(global_ids)

        global_list: List[Dict[str, Any]] = []
        rank_counter = 1
        # Rebuild in sorted order (and ensure current user is included once)
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
            # append current user (not top N)
            prof = global_profiles.get(student.id)
            if prof:
                # compute approximate rank accurately
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

        # ---- SCHOOL leaderboard (within same org) ----
        school_all = (PointTransaction.objects
                      .filter(student__organization=org)
                      .values("student_id")
                      .annotate(xp=Sum("points")))
        school_top = _top_rows(school_all, top_school)
        school_ids = [r["student_id"] for r in school_top]
        if student.id not in school_ids:
            school_ids.append(student.id)

        school_badges = _badge_count_map(school_ids)
        school_streaks = _streak_map(school_ids)
        school_profiles = _profiles_map(school_ids)

        school_list: List[Dict[str, Any]] = []
        rank_counter = 1
        seen = set()
        for row in sorted(school_top, key=lambda r: (-int(r["xp"] or 0), r["student_id"])):
            sid = row["student_id"]; seen.add(sid)
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

        # ---- WEEKLY leaderboard (points earned in last 7 days, within org) ----
        weekly_all = (PointTransaction.objects
                      .filter(student__organization=org, created_at__gte=week_since)
                      .values("student_id")
                      .annotate(xp=Sum("points")))
        weekly_top = _top_rows(weekly_all, top_weekly)
        weekly_ids = [r["student_id"] for r in weekly_top]
        if student.id not in weekly_ids:
            weekly_ids.append(student.id)

        weekly_profiles = _profiles_map(weekly_ids)
        weekly_streaks = _streak_map(weekly_ids)

        weekly_list: List[Dict[str, Any]] = []
        rank_counter = 1
        seen = set()
        for row in sorted(weekly_top, key=lambda r: (-int(r["xp"] or 0), r["student_id"])):
            sid = row["student_id"]; seen.add(sid)
            prof = weekly_profiles.get(sid)
            if not prof:
                continue
            weekly_list.append({
                "rank": rank_counter,
                "name": _name_from_user(prof.user),
                "points": int(row["xp"] or 0),  # "points this week"
                "avatar": _avatar_url(prof.user),
                "streak": weekly_streaks.get(sid, 0),
                "isCurrentUser": bool(sid == student.id),
            })
            rank_counter += 1

        # append current user weekly entry if missing
        if student.id not in seen:
            prof = weekly_profiles.get(student.id)
            if prof:
                my_week_pts = int(
                    PointTransaction.objects.filter(student=student, created_at__gte=week_since)
                    .aggregate(x=Sum("points")).get("x") or 0
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

        # ---- stats header ----
        global_rank = _rank_for_student(global_all, student.id, student_total_points)
        school_rank = _rank_for_student(school_all, student.id, student_total_points)
        weekly_points = next((i["points"] for i in weekly_list if i.get("isCurrentUser")), 0)

        # "competitors": active learners in school (students with any points in last 30 days)
        month_since = now - timedelta(days=30)
        competitors = (PointTransaction.objects
                       .filter(student__organization=org, created_at__gte=month_since)
                       .values("student_id").distinct().count())
        if competitors == 0:
            # fallback to count students in org
            competitors = StudentProfile.objects.filter(organization=org).count() or 0

        # ---- if everything is empty, provide nice dummies ----
        if not global_list and not school_list and not weekly_list:
            return Response(_dummy_payload(), status=status.HTTP_200_OK)

        payload = {
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
        err = {"detail": "Failed to load leaderboard.", "error": f"{type(e).__name__}: {e}"}
        if request.query_params.get("debug") in {"1", "true", "True"} or getattr(settings, "DEBUG", False):
            err["traceback"] = traceback.format_exc()
        return Response(err, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ---------- dummy payload (when no data/student) ----------
def _dummy_payload() -> Dict[str, Any]:
    return {
        "stats": {
            "global_rank": 4,
            "school_rank": 1,
            "total_points": 7500,
            "weekly_points": 450,
            "competitors": 2847,
        },
        "global": [
            {"rank": 1, "name": "Sarah Dan freak", "school": "Tech High School", "points": 15420,
             "avatar": None, "streak": 45, "badges": 12},
            {"rank": 2, "name": "Alex Rodriguez", "school": "Innovation Academy", "points": 14890,
             "avatar": None, "streak": 38, "badges": 11},
            {"rank": 3, "name": "Emma Thompson", "school": "Future Leaders School", "points": 14250,
             "avatar": None, "streak": 42, "badges": 10},
            {"rank": 4, "name": "John Doe", "school": "Your School", "points": 7500,
             "avatar": None, "streak": 15, "badges": 3, "isCurrentUser": True},
            {"rank": 5, "name": "Maria Garcia", "school": "Excellence Institute", "points": 13100,
             "avatar": None, "streak": 28, "badges": 9},
        ],
        "school": [
            {"rank": 1, "name": "John Doe", "points": 7500, "avatar": None, "streak": 15, "badges": 3, "isCurrentUser": True},
            {"rank": 2, "name": "Lisa Wang", "points": 6800, "avatar": None, "streak": 22, "badges": 5},
            {"rank": 3, "name": "Mike Johnson", "points": 6200, "avatar": None, "streak": 18, "badges": 4},
            {"rank": 4, "name": "Anna Smith", "points": 5900, "avatar": None, "streak": 12, "badges": 3},
            {"rank": 5, "name": "David Lee", "points": 5400, "avatar": None, "streak": 25, "badges": 6},
        ],
        "weekly": [
            {"rank": 1, "name": "John Doe", "points": 450, "avatar": None, "streak": 7, "isCurrentUser": True},
            {"rank": 2, "name": "Lisa Wang", "points": 380, "avatar": None, "streak": 6},
            {"rank": 3, "name": "Mike Johnson", "points": 320, "avatar": None, "streak": 5},
        ],
    }
