from typing import Any, Dict, List, Optional
import traceback
from decimal import Decimal
from datetime import timedelta, datetime
from django.http import JsonResponse
from django.conf import settings
from django.db.models import Q, Sum, Count, Avg, Max, Min
from django.utils import timezone
from django.contrib.auth.decorators import login_required
from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.response import Response
from rest_framework import status
from rest_framework_api_key.permissions import HasAPIKey
from api.authentication import SessionTokenAuthentication
from orgs.models import Organization, OrganizationMembership
from academics.models import StudentProfile, ParentProfile, TeacherProfile, Classroom, Subject
from learning.models import Course, Enrollment, Lesson
from assessments.models import Test, TestAttempt, TestAnswer, Question
from gamification.models import (Badge, BadgeAward, PointTransaction, Streak, 
    AchievementDefinition,AchievementAcquired,ActivityEvent
)
from attendance.models import AttendanceRecord, AttendanceSession
from core.utils import _get_student_for_user, _to_int, _sum_points
from django.db.models.functions import Cast
from gamification.services.streaks import build_streak
from texagonbackend.settings import pass_mark as PASS_MARK
from texagonbackend.settings import LOW_SCORE

@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def achievements_overview(request):
    try:
        user = request.user
        student = _get_student_for_user(user)
        if not student:
            return Response({}, status=status.HTTP_404_NOT_FOUND)

        org = getattr(student, "organization", None)


        org_id = getattr(org, "id", None)

        total_points = _sum_points(student)
        streak_obj = getattr(student, "streak", None)
        streak_current = int(getattr(streak_obj, "current_days", 0) or 0)
        streak_best = int(getattr(streak_obj, "longest_days", 0) or 0)

        # ---------------------------
        # BADGES (unchanged response)
        # ---------------------------
        badges_qs = Badge.objects.all()
        if org:
            badges_qs = badges_qs.filter(Q(organization=org) | Q(organization__isnull=True))
        badges_qs = badges_qs.order_by("points", "id")
        awarded_ids = set(
            BadgeAward.objects.filter(student=student, badge__in=badges_qs)
            .values_list("badge_id", flat=True)
        )
        badges_total = badges_qs.count()
        badges_earned = len(awarded_ids)

        badges = []
        for b in badges_qs:
            earned = (b.id in awarded_ids) or (total_points >= (b.points or 0))
            item = {
                "id": b.id,
                "name": b.name,
                "description": (b.criteria or f"Earned {b.points:,} points") if b.points else (b.criteria or ""),
                "icon": b.icon_name or "medal",
                "color": b.color or "bg-gray-400",
                "earned": bool(earned),
            }
            if not earned and (b.points or 0) > 0:
                item["progress"] = int(total_points)
                item["total"] = int(b.points)
            badges.append(item)
        # -----------------------------------------
        # ACHIEVEMENTS (rule-driven + acquired)
        # -----------------------------------------
        defs_qs = AchievementDefinition.objects.filter(is_active=True)
        if org:
            defs_qs = defs_qs.filter(Q(organization__isnull=True) | Q(organization=org))

        defs_map = {}
        for d in defs_qs.order_by("-organization_id", "id"):
            defs_map.setdefault(d.code, d)
        defs = list(defs_map.values())
        acquired_qs = (
            AchievementAcquired.objects
            .filter(student=student, definition__in=defs)
            .select_related("definition")
        )
        acquired_by_def_id = {a.definition_id: a for a in acquired_qs}
        def _rule_target(rule: dict) -> int:
            try:
                return int((rule or {}).get("target") or 0)
            except Exception:
                return 0

        def _apply_rule_filters(ev_qs, rule: dict):
            filters = (rule or {}).get("filters") or {}
            for k, v in filters.items():
                ev_qs = ev_qs.filter(**{f"meta__{k}": v})
            return ev_qs

        def _apply_window(ev_qs, rule: dict):
            window_days = (rule or {}).get("window_days")
            if not window_days:
                return ev_qs
            try:
                days = int(window_days)
            except Exception:
                return ev_qs
            since = timezone.now() - timedelta(days=days)  # ✅ FIX
            return ev_qs.filter(occurred_at__gte=since)

        def _compute_value(student_id: int, org_id: int, rule: dict) -> int:
            metric = (rule or {}).get("metric")
            event_type = (rule or {}).get("event_type")
            if not metric or not event_type:
                return 0

            ev_qs = ActivityEvent.objects.filter(
                student_id=student_id,
                organization_id=org_id,
                event_type=event_type,
            )
            ev_qs = _apply_window(ev_qs, rule)
            ev_qs = _apply_rule_filters(ev_qs, rule)
            if metric == "count":
                return int(ev_qs.count())
            if metric == "sum":
                return int(ev_qs.aggregate(s=Sum("value"))["s"] or 0)
            if metric == "max":
                return int(ev_qs.aggregate(m=Max("value"))["m"] or 0)
            if metric == "distinct_count":
                distinct_key = (rule or {}).get("distinct_key")
                if not distinct_key:
                    return 0
                return int(ev_qs.values(f"meta__{distinct_key}").distinct().count())
            if metric == "consecutive":
                return build_streak(ev_qs).count()
            return 0

        def build(defn: AchievementDefinition, earned: bool, progress: int = None, total: int = None, earned_date=None):
            return {
                "id": defn.id,
                "title": defn.title,
                "description": defn.description,
                "icon": defn.icon or "star",
                "earned": bool(earned),
                "earnedDate": (earned_date or (timezone.now().date().isoformat() if earned else None)),
                "points": int(defn.points or 0),
                "category": defn.category or "General",
                **({"progress": int(progress)} if progress is not None else {}),
                **({"total": int(total)} if total is not None else {}),
            }

        achievements = []
        for defn in defs:
            acquired = acquired_by_def_id.get(defn.id)
            earned = bool(acquired)

            earned_date = acquired.acquired_at.date().isoformat() if (acquired and acquired.acquired_at) else None
            
            rule = defn.rule or {}
            target = _rule_target(rule)

            value = 0
            if org_id:
                value = _compute_value(student.id, org_id, rule)

            if target > 0:
                achievements.append(build(defn, earned, progress=min(value, target), total=target, earned_date=earned_date))
            else:
                achievements.append(build(defn, earned, earned_date=earned_date))

        achievements_total = len(achievements)
        achievements_unlocked = sum(1 for a in achievements if a.get("earned"))

        payload = {
            "stats": {
                "total_points": int(total_points),
                "achievements_unlocked": int(achievements_unlocked),
                "achievements_total": int(achievements_total),
                "badges_earned": int(badges_earned),
                "badges_total": int(badges_total),
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
            import traceback
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




def r2(value, default=Decimal("0.00")) -> float:
    """
    Safe rounding helper:
    - accepts Decimal/float/int/None
    - returns float rounded to 2dp for JSON
    """
    if value is None:
        value = default
    try:
        return float(Decimal(str(value)).quantize(Decimal("0.01")))
    except (InvalidOperation, ValueError, TypeError):
        return float(Decimal(str(default)).quantize(Decimal("0.01")))


def get_test_difficulty(avg_score: Decimal | float | int) -> str:
    """
    Basic heuristic from actual data (no dummy).
    You can tweak thresholds anytime.
    """
    s = float(avg_score or 0)
    if s >= 80:
        return "Easy"
    if s >= 50:
        return "Medium"
    return "Hard"


def get_top_students_for_teacher_courses(teacher_courses_qs, limit=10):
    """
    Top students across the teacher's courses by average TestAttempt.score (graded only).
    Returns minimal fields the UI can use.
    """
    # All students who have graded attempts in tests belonging to the teacher's courses
    qs = (
        TestAttempt.objects.filter(
            status="graded",
            test__course__in=teacher_courses_qs,
        )
        .values("student_id", "student__user__first_name", "student__user__last_name", "student__user__email")
        .annotate(
            avg_score=Avg("score"),
            attempts=Count("id"),
        )
        .order_by("-avg_score", "-attempts")[:limit]
    )

    out = []
    for row in qs:
        full_name = f"{row.get('student__user__first_name') or ''} {row.get('student__user__last_name') or ''}".strip()
        out.append(
            {
                "id": str(row["student_id"]),
                "name": full_name or row.get("student__user__email") or f"student-{row['student_id']}",
                "avgScore": r2(row.get("avg_score")),
                "attempts": int(row.get("attempts") or 0),
            }
        )
    return out





def _d(value, default="0") -> Decimal:
    """Safe Decimal conversion."""
    try:
        return Decimal(str(value if value is not None else default))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(str(default))


def _pct(score: Decimal, total_points: Decimal) -> Decimal:
    """Convert attempt.score (raw) to percent using sum(question.points)."""
    if not total_points or total_points <= 0:
        return Decimal("0")
    return (score / total_points) * Decimal("100")


@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def teacher_analytics_view(request):
    """
    PATCHED ENDPOINT (full code):

    - "Active this month" card -> Pass Rate
    - Course Completions -> ONLY public courses (course_type='public')
    - Enrollment linkage -> ONLY Active/Completed (exclude Dropped)
    - Course performance adds passRate + returns modal fields to prevent "View Details" crash
    - Avg Score and Pass Rate computed from sum(test.questions.points) (NOT total_marks)
    - Attempts include status in ["submitted", "graded"] to avoid 0% when not graded
    """
    try:
        user = request.user
        
        PASS_MARK_PCT = Decimal(f"{PASS_MARK}")
        LOW_SCORE = Decimal("30")
        # Teacher profile
        try:
            teacher_profile = TeacherProfile.objects.select_related("organization").get(user=user)
        except TeacherProfile.DoesNotExist:
            return Response({"detail": "Teacher profile not found."}, status=status.HTTP_404_NOT_FOUND)

        organization = teacher_profile.organization

        # Teacher courses
        teacher_courses = (
            Course.objects.filter(
                teacher=teacher_profile,
                organization=organization,
                is_active=True,
            )
            .select_related("subject", "classroom")
        )

        valid_enrollment_statuses = [Enrollment.Status.ACTIVE, Enrollment.Status.COMPLETED]
        valid_attempt_statuses = ["submitted", "graded"]

        # -----------------------------
        # Precompute test -> total_points (sum of question.points) for all teacher tests
        # -----------------------------
        tests_qs = Test.objects.filter(course__in=teacher_courses)
        test_points_rows = (
            tests_qs.annotate(total_points=Sum("questions__points"))
            .values("id", "course_id", "total_points")
        )
        test_total_points = {row["id"]: _d(row["total_points"]) for row in test_points_rows}

        # Also map course -> list of test ids (for quick lookup)
        course_test_ids = {}
        for row in test_points_rows:
            course_test_ids.setdefault(row["course_id"], []).append(row["id"])

        # -----------------------------
        # Overall Stats
        # -----------------------------
        total_students = StudentProfile.objects.filter(organization=organization).count()

        # Overall pass rate across teacher courses (based on percent using question points)
        overall_attempts_rows = (
            TestAttempt.objects.filter(
                test__in=list(test_total_points.keys()),
                status__in=valid_attempt_statuses,
            )
            .values("score", "test_id")
        )

        overall_attempts = list(overall_attempts_rows)
        overall_attempts_count = len(overall_attempts)

        if overall_attempts_count == 0:
            overall_pass_rate = Decimal("0")
        else:
            pass_count = 0
            for a in overall_attempts:
                score = _d(a["score"])
                total_pts = test_total_points.get(a["test_id"], Decimal("0"))
                pct = _pct(score, total_pts)
                if pct >= PASS_MARK_PCT:
                    pass_count += 1
            overall_pass_rate = (Decimal(pass_count) / Decimal(overall_attempts_count)) * Decimal("100")

        # Course completions ONLY for public courses
        public_teacher_courses = teacher_courses.filter(course_type="public")
        course_completions = Enrollment.objects.filter(
            course__in=public_teacher_courses,
            status=Enrollment.Status.COMPLETED,
        ).count()

        # Frontend expects "change" (you can keep empty string to avoid dummy)
        overall_stats = [
            {
                "title": "Total Students",
                "value": str(total_students),
                "change": "",
                "icon": "Users",
                "color": "text-blue-600",
            },
            {
                "title": "Pass Rate",  # was "Active This Month"
                "value": f"{r2(overall_pass_rate)}%",
                "change": "",
                "icon": "TrendingUp",
                "color": "text-green-600",
            },
            {
                "title": "Course Completions",
                "value": str(course_completions),
                "change": "",
                "icon": "Award",
                "color": "text-orange-600",
            },
        ]

        # -----------------------------
        # Course Performance
        # -----------------------------
        course_performance = []

        # Preload enrollments for teacher courses (Active/Completed only)
        enrollments_rows = (
            Enrollment.objects.filter(course__in=teacher_courses, status__in=valid_enrollment_statuses)
            .select_related("student__user", "course")
        )

        # Group enrollments per course
        enrollments_by_course = {}
        for e in enrollments_rows:
            enrollments_by_course.setdefault(e.course_id, []).append(e)

        # Preload attempts for teacher courses (submitted/graded), group by course & student for averages
        attempts_rows = (
            TestAttempt.objects.filter(
                test__in=list(test_total_points.keys()),
                status__in=valid_attempt_statuses,
            )
            .values("student_id", "test_id", "score")
        )

        # Build course_id from test_id quickly
        # (we already have course_test_ids, but need reverse map test->course)
        test_course_map = {}
        for row in test_points_rows:
            test_course_map[row["id"]] = row["course_id"]

        # Accumulate percent scores per (course, student)
        # and per course overall (for avgScore and passRate)
        course_student_pct_sum = {}
        course_student_pct_count = {}

        course_pct_sum = {}
        course_attempt_count = {}
        course_pass_count = {}

        for a in attempts_rows:
            test_id = a["test_id"]
            course_id = test_course_map.get(test_id)
            if not course_id:
                continue

            score = _d(a["score"])
            total_pts = test_total_points.get(test_id, Decimal("0"))
            pct = _pct(score, total_pts)

            # per course overall
            course_pct_sum[course_id] = course_pct_sum.get(course_id, Decimal("0")) + pct
            course_attempt_count[course_id] = course_attempt_count.get(course_id, 0) + 1
            if pct >= PASS_MARK_PCT:
                course_pass_count[course_id] = course_pass_count.get(course_id, 0) + 1

            # per (course, student) for top/struggling lists
            key = (course_id, a["student_id"])
            course_student_pct_sum[key] = course_student_pct_sum.get(key, Decimal("0")) + pct
            course_student_pct_count[key] = course_student_pct_count.get(key, 0) + 1

        # Build per-course response
        for course in teacher_courses:
            course_id = course.id

            course_enrollments = enrollments_by_course.get(course_id, [])
            student_count = len(course_enrollments)

            completed_count = sum(1 for e in course_enrollments if e.status == Enrollment.Status.COMPLETED)
            completion_rate = (
                (Decimal(completed_count) / Decimal(student_count)) * Decimal("100")
                if student_count
                else Decimal("0")
            )

            # avgScore% (course)
            att_count = course_attempt_count.get(course_id, 0)
            if att_count == 0:
                avg_score_pct = Decimal("0")
                pass_rate_pct = Decimal("0")
            else:
                avg_score_pct = course_pct_sum.get(course_id, Decimal("0")) / Decimal(att_count)
                pass_rate_pct = (Decimal(course_pass_count.get(course_id, 0)) / Decimal(att_count)) * Decimal("100")

            # Modal lists (top/struggling) - based on attempt avg % + enrollment progress_pct for tie-breaker
            students_rows = []
            for e in course_enrollments[:5000]:
                student = e.student
                u = getattr(student, "user", None)

                # student's avg percent in this course
                s_key = (course_id, student.id)
                s_count = course_student_pct_count.get(s_key, 0)
                if s_count == 0:
                    s_avg_pct = Decimal("0")
                else:
                    s_avg_pct = course_student_pct_sum.get(s_key, Decimal("0")) / Decimal(s_count)

                last_login = getattr(u, "last_login", None) if u else None
                name = (u.get_full_name() or u.email) if u else f"student-{student.id}"

                students_rows.append(
                    {
                        "name": name,
                        "score": r2(s_avg_pct),
                        "progress": r2(getattr(e, "progress_pct", Decimal("0"))),
                        "lastActive": last_login.date().isoformat() if last_login else "N/A",
                    }
                )

            students_by_best = sorted(students_rows, key=lambda x: (x["score"], x["progress"]), reverse=True)

            # ONLY students below LOW_SCORE
            struggling_only = [s for s in students_rows if _d(s["score"]) < LOW_SCORE]
            struggling_by_worst = sorted(struggling_only, key=lambda x: (x["score"], x["progress"]))

            top_performers = [
                {"name": x["name"], "score": x["score"], "progress": x["progress"]}
                for x in students_by_best[:5]
            ]

            struggling_students = [
                {"name": x["name"], "score": x["score"], "progress": x["progress"], "lastActive": x["lastActive"]}
                for x in struggling_by_worst[:5]
            ]

            total_lessons = Lesson.objects.filter(module__course=course, active=True).count()

            course_performance.append(
                {
                    "id": str(course.id),
                    "name": course.name,
                    "students": int(student_count),
                    # Keep avgProgress if you still want it elsewhere; frontend can ignore it.
                    "avgProgress": 0.0,
                    "avgScore": r2(avg_score_pct),
                    "passRate": r2(pass_rate_pct),
                    "completionRate": r2(completion_rate),

                    # modal fields to prevent View Details crash
                    "rating": 0,
                    "totalLessons": int(total_lessons),
                    "completedLessons": 0,
                    "enrollmentTrend": [],
                    "weeklyActivity": [],
                    "topPerformers": top_performers,
                    "strugglingStudents": struggling_students,
                }
            )

        # -----------------------------
        # Top Students (tab)
        # Must return: {name, coursesCompleted, avgScore, lastActive}
        # Using percent scores across all teacher course attempts
        # -----------------------------
        # Aggregate percent per student from earlier attempts loop by reusing course_student_pct_sum/count
        student_pct_sum = {}
        student_pct_count = {}

        for (course_id, student_id), s_sum in course_student_pct_sum.items():
            s_count = course_student_pct_count.get((course_id, student_id), 0)
            student_pct_sum[student_id] = student_pct_sum.get(student_id, Decimal("0")) + s_sum
            student_pct_count[student_id] = student_pct_count.get(student_id, 0) + s_count

        # Candidate students are those enrolled (Active/Completed) in teacher courses
        candidate_student_ids = list(
            Enrollment.objects.filter(course__in=teacher_courses, status__in=valid_enrollment_statuses)
            .values_list("student_id", flat=True)
            .distinct()
        )

        # Build rows
        top_rows = []
        profiles = (
            StudentProfile.objects.filter(id__in=candidate_student_ids)
            .select_related("user")
        )

        # Precompute coursesCompleted per student (teacher courses only)
        completed_counts = {
            row["student_id"]: row["c"]
            for row in Enrollment.objects.filter(
                course__in=teacher_courses,
                status=Enrollment.Status.COMPLETED,
            )
            .values("student_id")
            .annotate(c=Count("id"))
        }

        for p in profiles:
            sid = p.id
            cnt = student_pct_count.get(sid, 0)
            avg_pct = (student_pct_sum.get(sid, Decimal("0")) / Decimal(cnt)) if cnt else Decimal("0")

            u = p.user
            last_login = getattr(u, "last_login", None)
            name = u.get_full_name() or u.email

            top_rows.append(
                {
                    "name": name,
                    "coursesCompleted": int(completed_counts.get(sid, 0)),
                    "avgScore": r2(avg_pct),
                    "lastActive": last_login.date().isoformat() if last_login else "N/A",
                }
            )

        top_students = sorted(top_rows, key=lambda x: (x["avgScore"], x["coursesCompleted"]), reverse=True)[:10]

        # -----------------------------
        # Test Analytics (percent-based using question points)
        # -----------------------------
        test_analytics = []
        published_tests = Test.objects.filter(
            course__in=teacher_courses,
            visibility=Test.Visibility.PUBLISHED,
        )

        # Precompute total_points for published tests
        published_points = {
            row["id"]: _d(row["total_points"])
            for row in published_tests.annotate(total_points=Sum("questions__points")).values("id", "total_points")
        }

        for test in published_tests[:10]:
            total_pts = published_points.get(test.id, Decimal("0"))

            attempts = list(
                TestAttempt.objects.filter(test=test, status__in=valid_attempt_statuses)
                .values("score")
            )
            total_attempts = len(attempts)

            if total_attempts == 0:
                test_analytics.append(
                    {
                        "id": str(test.id),
                        "name": test.title,
                        "attempts": 0,
                        "avgScore": 0.00,
                        "passRate": 0.00,
                        "difficulty": "N/A",
                        "questions": test.questions.count(),
                        "timeLimit": f"{test.duration_minutes} minutes",
                        "scoreDistribution": [
                            {"range": "90-100", "count": 0},
                            {"range": "80-89", "count": 0},
                            {"range": "70-79", "count": 0},
                            {"range": "60-69", "count": 0},
                            {"range": "Below 60", "count": 0},
                        ],
                    }
                )
                continue

            pct_sum = Decimal("0")
            pass_count = 0
            buckets = {"90-100": 0, "80-89": 0, "70-79": 0, "60-69": 0, "Below 60": 0}

            for a in attempts:
                pct = _pct(_d(a["score"]), total_pts)
                pct_sum += pct
                if pct >= PASS_MARK_PCT:
                    pass_count += 1

                if pct >= 90:
                    buckets["90-100"] += 1
                elif pct >= 80:
                    buckets["80-89"] += 1
                elif pct >= 70:
                    buckets["70-79"] += 1
                elif pct >= 60:
                    buckets["60-69"] += 1
                else:
                    buckets["Below 60"] += 1

            avg_score_pct = pct_sum / Decimal(total_attempts)
            pass_rate_pct = (Decimal(pass_count) / Decimal(total_attempts)) * Decimal("100")
            score_distribution = [{"range": k, "count": v} for k, v in buckets.items()]

            test_analytics.append(
                {
                    "id": str(test.id),
                    "name": test.title,
                    "attempts": int(total_attempts),
                    "avgScore": r2(avg_score_pct),
                    "passRate": r2(pass_rate_pct),
                    "difficulty": get_test_difficulty(avg_score_pct),
                    "questions": test.questions.count(),
                    "timeLimit": f"{test.duration_minutes} minutes",
                    "scoreDistribution": score_distribution,
                }
            )

        popular_content = []
    except Exception as e:
        print(e)
        return Response({}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    return Response(
        {
            "overallStats": overall_stats,
            "coursePerformance": course_performance,
            "topStudents": top_students,
            "testAnalytics": test_analytics,
            "popularContent": popular_content,
        }
    )


def get_top_performers(course):
    """Get top 3 performing students for a course"""
    enrollments = Enrollment.objects.filter(
        course=course, 
        status='active'
    ).select_related('student__user').order_by('-progress_pct')[:3]
    
    performers = []
    for enrollment in enrollments:
        # Get average test score for this student in this course
        test_attempts = TestAttempt.objects.filter(
            test__course=course,
            student=enrollment.student,
            status='graded'
        )
        avg_score = test_attempts.aggregate(avg_score=Avg('score'))['avg_score'] or 0
        
        performers.append({
            "name": enrollment.student.user.get_full_name() or enrollment.student.user.username,
            "score": round(float(avg_score), 1),
            "progress": round(float(enrollment.progress_pct), 1)
        })
    
    return performers


def get_struggling_students(course):
    """Get students who need help in a course"""
    enrollments = Enrollment.objects.filter(
        course=course,
        status='active',
        progress_pct__lt=50
    ).select_related('student__user').order_by('progress_pct')[:3]
    
    struggling = []
    for enrollment in enrollments:
        # Get average test score
        test_attempts = TestAttempt.objects.filter(
            test__course=course,
            student=enrollment.student,
            status='graded'
        )
        avg_score = test_attempts.aggregate(avg_score=Avg('score'))['avg_score'] or 0
        
        # Calculate last active (dummy data)
        last_active_options = ["3 days ago", "1 week ago", "2 days ago", "5 days ago", "4 days ago"]
        import random
        last_active = random.choice(last_active_options)
        
        struggling.append({
            "name": enrollment.student.user.get_full_name() or enrollment.student.user.username,
            "score": round(float(avg_score), 1),
            "progress": round(float(enrollment.progress_pct), 1),
            "lastActive": last_active
        })
    
    return struggling


def get_overall_top_students(courses):
    """Get top students across all teacher's courses"""
    # Get all students enrolled in teacher's courses
    enrollments = Enrollment.objects.filter(
        course__in=courses,
        status__in=['active', 'completed']
    ).select_related('student__user')
    
    # Group by student and calculate metrics
    student_metrics = {}
    for enrollment in enrollments:
        student_id = enrollment.student.id
        if student_id not in student_metrics:
            student_metrics[student_id] = {
                "name": enrollment.student.user.get_full_name() or enrollment.student.user.username,
                "coursesCompleted": 0,
                "total_score": 0,
                "score_count": 0,
                "lastActive": "2 hours ago"  # Dummy data
            }
        
        if enrollment.status == 'completed':
            student_metrics[student_id]["coursesCompleted"] += 1
        
        # Get test scores for this student
        test_attempts = TestAttempt.objects.filter(
            test__course=enrollment.course,
            student=enrollment.student,
            status='graded'
        )
        
        for attempt in test_attempts:
            student_metrics[student_id]["total_score"] += float(attempt.score)
            student_metrics[student_id]["score_count"] += 1
    
    # Calculate average scores and sort
    top_students = []
    for student_id, metrics in student_metrics.items():
        if metrics["score_count"] > 0:
            avg_score = metrics["total_score"] / metrics["score_count"]
            top_students.append({
                "name": metrics["name"],
                "coursesCompleted": metrics["coursesCompleted"],
                "avgScore": round(avg_score, 1),
                "lastActive": metrics["lastActive"]
            })
    
    # Sort by average score and return top 10
    top_students.sort(key=lambda x: x["avgScore"], reverse=True)
    return top_students[:10]


def get_test_difficulty(avg_score):
    """Determine test difficulty based on average score"""
    if avg_score >= 80:
        return "Easy"
    elif avg_score >= 60:
        return "Medium"
    else:
        return "Hard"


def get_common_mistakes(test):
    """Get common mistakes for a test (dummy data as requested)"""
    mistakes_options = [
        [
            {"question": "useState Hook Implementation", "incorrectRate": 34},
            {"question": "Component Lifecycle Methods", "incorrectRate": 28},
            {"question": "Props vs State Concepts", "incorrectRate": 22}
        ],
        [
            {"question": "Closures and Scope", "incorrectRate": 42},
            {"question": "Async/Await Patterns", "incorrectRate": 38},
            {"question": "Prototype Inheritance", "incorrectRate": 35}
        ],
        [
            {"question": "List Comprehensions", "incorrectRate": 18},
            {"question": "Dictionary Methods", "incorrectRate": 15},
            {"question": "String Formatting", "incorrectRate": 12}
        ]
    ]
    
    import random
    return random.choice(mistakes_options)


def get_performance_by_time():
    """Get performance by time of day (dummy data as requested)"""
    return [
        {"hour": "9 AM", "avgScore": 82, "attempts": 23},
        {"hour": "12 PM", "avgScore": 79, "attempts": 45},
        {"hour": "3 PM", "avgScore": 76, "attempts": 67},
        {"hour": "6 PM", "avgScore": 81, "attempts": 89},
        {"hour": "9 PM", "avgScore": 74, "attempts": 34}
    ]


#@login_required
def generate_subs(request):
    now = timezone.now()
    qs = ParentProfile.objects.select_related("organization_subscription__plan").filter(
        organization_subscription__isnull=False,
        organization_subscription__status=ParentProfile.organization_subscription.field.related_model.Status.ACTIVE
    )
    # above filter uses model attr for clarity; you can replace with literal "active"
    total_created = 0
    for parent in qs:
        try:
            created = parent.generate_subscription_invoices(now=now)
            total_created += len(created)

        except Exception as e:
            pass

    return JsonResponse({"total_created":total_created})



