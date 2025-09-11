# api/views.py
from typing import Any, Dict, List, Optional
import traceback
from decimal import Decimal

from django.conf import settings
from django.db.models import Q, Sum, Count
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.response import Response
from rest_framework import status
from rest_framework_api_key.permissions import HasAPIKey

from api.authentication import SessionTokenAuthentication

# 🔧 adjust imports to your app labels/namespaces
from orgs.models import OrganizationMembership
from academics.models import StudentProfile
from learning.models import Enrollment
from assessments.models import TestAttempt, Question
from gamification.models import Badge, BadgeAward, PointTransaction, Streak  # or wherever you placed them
from core.utils import _get_student_for_user, _to_int, _sum_points


def _completed_courses(student: StudentProfile) -> int:
    return int(
        Enrollment.objects
        .filter(student=student)
        .filter(Q(status="completed") | Q(progress_pct__gte=100))
        .count()
    )


# ---------------- endpoint ----------------
@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def achievements_overview(request):
    """
    Data for the Achievements UI (achievements.tsx).
    Response keys:
      - stats: { total_points, achievements_unlocked, achievements_total, badges_earned, badges_total,
                 streak_current, streak_best }
      - achievements: list of items like the component uses
          { id, title, description, icon, earned, earnedDate?, points, category, progress?, total? }
      - badges: list of items like the component uses
          { id, name, description, icon, color, earned, progress?, total? }

    Query params:
      - debug=1 → include traceback on error
    """
    try:
        user = request.user
        student = _get_student_for_user(user)

        # If we can't resolve a student, return rich dummy data so UI still renders
        if not student:
            return Response(_dummy_payload(), status=status.HTTP_200_OK)

        # ---- core aggregates ----
        org = getattr(student, "organization", None)
        total_points = _sum_points(student)
        streak_obj = getattr(student, "streak", None)
        streak_current = getattr(streak_obj, "current_days", 0) or 0
        streak_best = getattr(streak_obj, "longest_days", 0) or 0

        badges_qs = Badge.objects.all()
        if org:
            badges_qs = badges_qs.filter(organization=org)
        badges_total = badges_qs.count()
        badges_earned = BadgeAward.objects.filter(student=student).count()

        # ---- derive some achievement progress from real data (and fill with dummies if missing) ----

        # 1) First Steps: earned if any progress or any attempt/bookmark exists
        has_progress = Enrollment.objects.filter(student=student, progress_pct__gt=0).exists()
        has_attempt = TestAttempt.objects.filter(student=student).exists()
        first_steps_earned = bool(has_progress or has_attempt)

        # 2) Quiz Master: attempts with score >= 90% of test total (Question sum)
        attempts = list(TestAttempt.objects.filter(student=student).select_related("test"))
        test_ids = {a.test_id for a in attempts}
        totals_map: Dict[int, Decimal] = {
            tid: (Question.objects.filter(test_id=tid).aggregate(t=Sum("points")).get("t") or Decimal(0))
            for tid in test_ids
        }
        ninety_or_more = 0
        for a in attempts:
            total = float(totals_map.get(a.test_id) or 0)
            if total > 0 and float(a.score or 0) >= 0.9 * total:
                ninety_or_more += 1

        # 3) Streak Champion: 30-day goal
        streak_goal = 30

        # 4) Course Conqueror: 3 completed courses
        completed_courses = _completed_courses(student)
        conqueror_goal = 3

        # 5) Code Warrior: dummy “exercises” → proxy with submitted attempts count vs 10
        exercises_done = TestAttempt.objects.filter(student=student, status="submitted").count()
        exercises_goal = 10

        achievements: List[Dict[str, Any]] = [
            {
                "id": 1,
                "title": "First Steps",
                "description": "Complete your first lesson",
                "icon": "star",
                "earned": first_steps_earned,
                "earnedDate": timezone.now().date().isoformat() if first_steps_earned else None,
                "points": 50,
                "category": "Getting Started",
            },
            {
                "id": 2,
                "title": "Code Warrior",
                "description": "Complete 10 coding exercises",
                "icon": "trophy",
                "earned": exercises_done >= exercises_goal,
                "earnedDate": timezone.now().date().isoformat() if exercises_done >= exercises_goal else None,
                "points": 200,
                "category": "Coding",
                "progress": exercises_done if exercises_done < exercises_goal else exercises_goal,
                "total": exercises_goal,
            },
            {
                "id": 3,
                "title": "Quiz Master",
                "description": "Score 90% or higher on 5 quizzes",
                "icon": "target",
                "earned": ninety_or_more >= 5,
                "earnedDate": timezone.now().date().isoformat() if ninety_or_more >= 5 else None,
                "points": 300,
                "category": "Assessment",
                "progress": ninety_or_more if ninety_or_more < 5 else 5,
                "total": 5,
            },
            {
                "id": 4,
                "title": "Streak Champion",
                "description": "Maintain a 30-day learning streak",
                "icon": "zap",
                "earned": streak_current >= streak_goal,
                "earnedDate": timezone.now().date().isoformat() if streak_current >= streak_goal else None,
                "points": 500,
                "category": "Consistency",
                "progress": int(streak_current if streak_current < streak_goal else streak_goal),
                "total": streak_goal,
            },
            {
                "id": 5,
                "title": "Course Conqueror",
                "description": "Complete 3 full courses",
                "icon": "award",
                "earned": completed_courses >= conqueror_goal,
                "earnedDate": timezone.now().date().isoformat() if completed_courses >= conqueror_goal else None,
                "points": 750,
                "category": "Completion",
                "progress": int(completed_courses if completed_courses < conqueror_goal else conqueror_goal),
                "total": conqueror_goal,
            },
            {
                "id": 6,
                "title": "Peer Helper",
                "description": "Help 10 fellow students",
                "icon": "medal",
                "earned": False,  # no data source → dummy in-progress
                "points": 400,
                "category": "Community",
                "progress": 3,
                "total": 10,
            },
        ]

        # ---- badges: compute against point thresholds (and mark earned via awards too) ----
        # We keep the same look as the sample: Bronze/Silver/Gold/Diamond with thresholds.
        thresholds = [
            {"id": 1, "name": "Bronze Learner",  "icon": "medal",  "color": "bg-amber-600", "points": 1000},
            {"id": 2, "name": "Silver Scholar",  "icon": "trophy", "color": "bg-gray-400", "points": 5000},
            {"id": 3, "name": "Gold Graduate",   "icon": "crown",  "color": "bg-yellow-500", "points": 10000},
            {"id": 4, "name": "Diamond Elite",   "icon": "gem",    "color": "bg-blue-500",   "points": 25000},
        ]
        badges: List[Dict[str, Any]] = []
        for th in thresholds:
            earned = total_points >= th["points"]
            item = {
                "id": th["id"],
                "name": th["name"],
                "description": f"Earned {th['points']:,} points",
                "icon": th["icon"],
                "color": th["color"],
                "earned": earned,
            }
            if not earned:
                item["progress"] = total_points
                item["total"] = th["points"]
            badges.append(item)

        # If you want to also surface org-defined badges as extra items (optional):
        # Uncomment to append up to 4 organization badges with earned flag.
        # org_badges = badges_qs.select_related()[:4]
        # awarded_ids = set(BadgeAward.objects.filter(student=student, badge__in=org_badges).values_list("badge_id", flat=True))
        # for b in org_badges:
        #     badges.append({
        #         "id": 1000 + b.id,
        #         "name": b.name,
        #         "description": b.criteria or "Organization badge",
        #         "icon": "medal",
        #         "color": "bg-purple-500",
        #         "earned": b.id in awarded_ids,
        #     })

        achievements_total = len(achievements)
        achievements_unlocked = sum(1 for a in achievements if a.get("earned"))

        payload = {
            "stats": {
                "total_points": total_points,
                "achievements_unlocked": achievements_unlocked,
                "achievements_total": achievements_total,
                "badges_earned": badges_earned,
                "badges_total": max(badges_total, len(thresholds)),  # include threshold set
                "streak_current": int(streak_current),
                "streak_best": int(streak_best),
            },
            "achievements": achievements,
            "badges": badges,
        }
        return Response(payload, status=status.HTTP_200_OK)

    except Exception as e:
        err = {"detail": "Failed to load achievements.", "error": f"{type(e).__name__}: {e}"}
        if request.query_params.get("debug") in {"1", "true", "True"} or getattr(settings, "DEBUG", False):
            err["traceback"] = traceback.format_exc()
        return Response(err, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ---------------- dummy fallback (no student) ----------------
def _dummy_payload() -> Dict[str, Any]:
    achievements = [
        {"id": 1, "title": "First Steps", "description": "Complete your first lesson", "icon": "star",
         "earned": True, "earnedDate": "2024-01-15", "points": 50, "category": "Getting Started"},
        {"id": 2, "title": "Code Warrior", "description": "Complete 10 coding exercises", "icon": "trophy",
         "earned": True, "earnedDate": "2024-01-20", "points": 200, "category": "Coding"},
        {"id": 3, "title": "Quiz Master", "description": "Score 90% or higher on 5 quizzes", "icon": "target",
         "earned": False, "progress": 3, "total": 5, "points": 300, "category": "Assessment"},
        {"id": 4, "title": "Streak Champion", "description": "Maintain a 30-day learning streak", "icon": "zap",
         "earned": False, "progress": 15, "total": 30, "points": 500, "category": "Consistency"},
        {"id": 5, "title": "Course Conqueror", "description": "Complete 3 full courses", "icon": "award",
         "earned": False, "progress": 1, "total": 3, "points": 750, "category": "Completion"},
        {"id": 6, "title": "Peer Helper", "description": "Help 10 fellow students", "icon": "medal",
         "earned": True, "earnedDate": "2024-01-25", "points": 400, "category": "Community"},
    ]
    badges = [
        {"id": 1, "name": "Bronze Learner", "description": "Earned 1,000 points", "icon": "medal",
         "color": "bg-amber-600", "earned": True},
        {"id": 2, "name": "Silver Scholar", "description": "Earned 5,000 points", "icon": "trophy",
         "color": "bg-gray-400", "earned": True},
        {"id": 3, "name": "Gold Graduate", "description": "Earned 10,000 points", "icon": "crown",
         "color": "bg-yellow-500", "earned": False, "progress": 7500, "total": 10000},
        {"id": 4, "name": "Diamond Elite", "description": "Earned 25,000 points", "icon": "gem",
         "color": "bg-blue-500", "earned": False, "progress": 7500, "total": 25000},
    ]
    return {
        "stats": {
            "total_points": 7500,
            "achievements_unlocked": 3,
            "achievements_total": len(achievements),
            "badges_earned": 2,
            "badges_total": len(badges),
            "streak_current": 15,
            "streak_best": 23,
        },
        "achievements": achievements,
        "badges": badges,
    }
