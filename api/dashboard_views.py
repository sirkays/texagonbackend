from django.utils import timezone
from django.db.models import Sum, Count, Q
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.response import Response
from rest_framework_api_key.permissions import HasAPIKey

from api.authentication import SessionTokenAuthentication
from api.permissions import RequiresActiveStudentSubscription
from core.utils import _get_student_for_user, resolve_season
from learning.models import Enrollment, Lesson
from gamification.models import PointTransaction, BadgeAward, Streak, AchievementAcquired, AchievementDefinition
from academics.models import EnrollmentCertificate
from assessments.models import Test
from live.models import LiveSession, TutoringBooking

@api_view(["GET"])
@permission_classes([HasAPIKey, RequiresActiveStudentSubscription()])
@authentication_classes([SessionTokenAuthentication])
def student_dashboard_overview(request):
    try:
        user = request.user
        student = _get_student_for_user(user)
        if not student:
            return Response({"detail": "Student profile not found."}, status=status.HTTP_404_NOT_FOUND)

        org = student.organization
        now = timezone.now()
        season = resolve_season(org, now)

        def _season_filter(qs, season_obj):
            return qs.filter(season=season_obj) if season_obj else qs

        # ---------------- STATS ----------------
        courses_enrolled = Enrollment.objects.filter(student=student, status=Enrollment.Status.ACTIVE).count()
        
        badges_earned_qs = BadgeAward.objects.filter(student=student)
        badges_earned = _season_filter(badges_earned_qs, season).count()
        
        certificates = EnrollmentCertificate.objects.filter(
            student=student, status=EnrollmentCertificate.Status.ISSUED
        ).count()
        
        streak_obj = Streak.objects.filter(student=student, season=season).first()
        streak_days = streak_obj.current_days if streak_obj else 0

        # ---------------- GAMIFICATION ----------------
        xp_qs = PointTransaction.objects.filter(student=student)
        xp = int(_season_filter(xp_qs, season).aggregate(x=Sum("points")).get("x") or 0)
        
        # Leveling via TieringService (reads Tier rows from DB)
        from core.models import TieringService
        level_info = TieringService.level_for_xp(xp)
        level_name = level_info["level_name"]
        progress_to_next_pct = level_info["progress_to_next_pct"]
        xp_to_next = level_info["xp_to_next"]

        achievements_unlocked = _season_filter(AchievementAcquired.objects.filter(student=student), season).count()
        achievements_total = AchievementDefinition.objects.filter(
            Q(organization=org) | Q(organization__isnull=True),
            is_active=True
        ).count()

        recent_achievement_obj = _season_filter(AchievementAcquired.objects.filter(student=student), season).order_by("-acquired_at").first()
        recent_achievement = recent_achievement_obj.definition.title if recent_achievement_obj else "None yet"

        # Ranks (using logic similar to gamification.views)
        global_all = _season_filter(PointTransaction.objects.all(), season).values("student_id").annotate(xp=Sum("points"))
        higher_global = global_all.filter(xp__gt=xp).count()
        global_rank = higher_global + 1

        school_all = _season_filter(PointTransaction.objects.filter(student__organization=org), season).values("student_id").annotate(xp=Sum("points"))
        higher_school = school_all.filter(xp__gt=xp).count()
        org_rank = higher_school + 1

        # ---------------- CONTINUE LEARNING ----------------
        # Get the most recently enrolled active course (or from bookmarks if we had recent activity tracking)
        recent_enrollment = Enrollment.objects.filter(student=student, status=Enrollment.Status.ACTIVE).order_by("-updated_at").first()
        continue_learning = None
        if recent_enrollment:
            course = recent_enrollment.course
            continue_learning = {
                "courseName": course.name,
                "lessonName": "Next Lesson", # Could be refined if we track last accessed lesson
                "progressPct": float(recent_enrollment.progress_pct),
                "courseId": course.id,
            }

        # ---------------- UPCOMING TESTS ----------------
        enrolled_course_ids = Enrollment.objects.filter(student=student, status=Enrollment.Status.ACTIVE).values_list("course_id", flat=True)
        upcoming_tests_qs = Test.objects.filter(
            course_id__in=enrolled_course_ids,
            start_at__gte=now
        ).order_by("start_at")[:3]
        
        upcoming_tests = []
        for test in upcoming_tests_qs:
            upcoming_tests.append({
                "id": test.id,
                "title": test.title,
                "courseName": test.course.name,
                "date": test.start_at.isoformat() if test.start_at else None,
                "durationMinutes": test.duration_minutes,
            })

        # ---------------- UPCOMING SESSIONS ----------------
        # Check both LiveSessions for enrolled courses and TutoringBookings
        upcoming_sessions = []
        live_sessions = LiveSession.objects.filter(
            course_id__in=enrolled_course_ids,
            scheduled_at__gte=now
        ).order_by("scheduled_at")[:2]
        
        for ls in live_sessions:
            upcoming_sessions.append({
                "id": ls.id,
                "title": ls.title,
                "type": "Class Session",
                "date": ls.scheduled_at.isoformat() if ls.scheduled_at else None,
                "joinUrl": ls.join_url,
            })

        tutoring_bookings = TutoringBooking.objects.filter(
            student=student,
            status=TutoringBooking.Status.CONFIRMED
        ).order_by("-created_at")[:2]

        for tb in tutoring_bookings:
            upcoming_sessions.append({
                "id": tb.id,
                "title": f"Tutoring with {tb.teacher.user.get_full_name() or 'Teacher'}",
                "type": "Private Tutoring",
                "date": tb.created_at.isoformat() if tb.created_at else None,
                "joinUrl": "",
            })

        upcoming_sessions = sorted(upcoming_sessions, key=lambda x: x["date"])[:3]

        return Response({
            "firstName": user.first_name or user.username,
            "stats": {
                "coursesEnrolled": courses_enrolled,
                "badgesEarned": badges_earned,
                "certificates": certificates,
                "streakDays": streak_days,
            },
            "gamification": {
                "xp": xp,
                "levelName": level_name,
                "progressToNextPct": progress_to_next_pct,
                "xpToNext": xp_to_next,
                "achievementsUnlocked": achievements_unlocked,
                "achievementsTotal": achievements_total,
                "recentAchievement": recent_achievement,
                "orgRank": org_rank,
                "globalRank": global_rank,
            },
            "continueLearning": continue_learning,
            "upcomingTests": upcoming_tests,
            "upcomingSessions": upcoming_sessions,
        }, status=status.HTTP_200_OK)

    except Exception as e:
        import traceback
        print("Error in student_dashboard_overview:")
        print(traceback.format_exc())
        return Response({"detail": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
