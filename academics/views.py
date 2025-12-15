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
from learning.models import Course, Enrollment
from assessments.models import Test, TestAttempt, TestAnswer, Question
from gamification.models import (Badge, BadgeAward, PointTransaction, Streak, 
    AchievementDefinition,AchievementAcquired,ActivityEvent
)
from attendance.models import AttendanceRecord, AttendanceSession
from core.utils import _get_student_for_user, _to_int, _sum_points
from django.db.models.functions import Cast


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
                # special-case: allow streak without events if you use event_type="streak_current"
                if (rule or {}).get("event_type") == "streak_current":
                    value = int(streak_current)
                else:
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




@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def teacher_analytics_view(request):
    """
    Comprehensive analytics endpoint for teacher dashboard
    Returns all data needed by the teacher-student-analytics frontend component
    """
    user = request.user
    
    # Get teacher profile and organization
    try:
        teacher_profile = TeacherProfile.objects.get(user=user)
        organization = teacher_profile.organization
    except TeacherProfile.DoesNotExist:
        return Response({"detail": "Teacher profile not found."}, status=status.HTTP_404_NOT_FOUND)

    # Get teacher's courses
    teacher_courses = Course.objects.filter(
        teacher=teacher_profile,
        organization=organization,
        is_active=True
    ).select_related('subject', 'classroom')

    # Calculate overall statistics
    total_students = StudentProfile.objects.filter(organization=organization).count()
    
    # Active students (those with activity in last 30 days)
    thirty_days_ago = timezone.now() - timedelta(days=30)
    active_students = StudentProfile.objects.filter(
        organization=organization,
        user__last_login__gte=thirty_days_ago
    ).count()
    
    # Course completions (students with >90% progress)
    course_completions = Enrollment.objects.filter(
        course__in=teacher_courses,
        progress_pct__gte=90,
        status='completed'
    ).count()

    overall_stats = [
        {
            "title": "Total Students",
            "value": str(total_students),
            "change": "+12%",  # Dummy data as requested
            "icon": "Users",
            "color": "text-blue-600"
        },
        {
            "title": "Active This Month", 
            "value": str(active_students),
            "change": "+8%",
            "icon": "TrendingUp",
            "color": "text-green-600"
        },
        {
            "title": "Course Completions",
            "value": str(course_completions),
            "change": "+23",
            "icon": "Award", 
            "color": "text-orange-600"
        }
    ]

    # Course performance data
    course_performance = []
    for course in teacher_courses:
        enrollments = Enrollment.objects.filter(course=course, status='active')
        student_count = enrollments.count()
        
        if student_count > 0:
            avg_progress = enrollments.aggregate(avg_progress=Avg('progress_pct'))['avg_progress'] or 0
            
            # Get test scores for this course
            test_attempts = TestAttempt.objects.filter(
                test__course=course,
                status='graded'
            )
            avg_score = test_attempts.aggregate(avg_score=Avg('score'))['avg_score'] or 0
            
            completion_rate = enrollments.filter(progress_pct__gte=90).count() / student_count * 100
            
            # Generate dummy data for missing fields
            course_data = {
                "id": str(course.id),
                "name": course.name,
                "students": student_count,
                "avgProgress": round(float(avg_progress), 1),
                "avgScore": round(float(avg_score), 1),
                "completionRate": round(completion_rate, 1),
                "rating": 4.7,  # Dummy data
                "totalLessons": 45,  # Dummy data
                "completedLessons": 35,  # Dummy data
                "enrollmentTrend": [20, 35, 45, 52, 48, 56, 62],  # Dummy data
                "weeklyActivity": [
                    {"day": "Monday", "active": 89},
                    {"day": "Tuesday", "active": 76}, 
                    {"day": "Wednesday", "active": 94},
                    {"day": "Thursday", "active": 82},
                    {"day": "Friday", "active": 67},
                    {"day": "Saturday", "active": 34},
                    {"day": "Sunday", "active": 45}
                ],
                "topPerformers": get_top_performers(course),
                "strugglingStudents": get_struggling_students(course)
            }
            course_performance.append(course_data)

    # Top students across all courses
    top_students = get_overall_top_students(teacher_courses)

    # Test analytics
    test_analytics = []
    tests = Test.objects.filter(course__in=teacher_courses, visibility='published')
    
    for test in tests[:10]:  # Limit to 10 tests
        attempts = TestAttempt.objects.filter(test=test)
        total_attempts = attempts.count()
        
        if total_attempts > 0:
            avg_score = attempts.aggregate(avg_score=Avg('score'))['avg_score'] or 0
            pass_rate = attempts.filter(score__gte=60).count() / total_attempts * 100
            
            # Score distribution
            score_distribution = [
                {"range": "90-100", "count": attempts.filter(score__gte=90).count()},
                {"range": "80-89", "count": attempts.filter(score__gte=80, score__lt=90).count()},
                {"range": "70-79", "count": attempts.filter(score__gte=70, score__lt=80).count()},
                {"range": "60-69", "count": attempts.filter(score__gte=60, score__lt=70).count()},
                {"range": "Below 60", "count": attempts.filter(score__lt=60).count()}
            ]
            
            test_data = {
                "id": str(test.id),
                "name": test.title,
                "attempts": total_attempts,
                "avgScore": round(float(avg_score), 1),
                "passRate": round(pass_rate, 1),
                "difficulty": get_test_difficulty(avg_score),
                "questions": test.questions.count(),
                "timeLimit": f"{test.duration_minutes} minutes",
                "scoreDistribution": score_distribution,
                "commonMistakes": get_common_mistakes(test),  # Dummy data
                "performanceByTime": get_performance_by_time()  # Dummy data
            }
            test_analytics.append(test_data)

    # Popular content (dummy data as requested)
    popular_content = [
        {"title": "React Hooks Tutorial", "views": 1234, "type": "Video"},
        {"title": "Python Cheat Sheet", "downloads": 890, "type": "PDF"},
        {"title": "JavaScript Fundamentals", "views": 756, "type": "Course"},
        {"title": "CSS Grid Guide", "views": 645, "type": "Tutorial"},
        {"title": "Database Design Principles", "views": 523, "type": "Video"}
    ]

    return Response({
        "overallStats": overall_stats,
        "coursePerformance": course_performance,
        "topStudents": top_students,
        "testAnalytics": test_analytics,
        "popularContent": popular_content
    })


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



