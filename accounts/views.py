from django.shortcuts import render
from orgs.models import OrganizationMembership 
from django.conf import settings
from api.retrieve_token import get_token_from_header
from api.authentication import SessionTokenAuthentication
from rest_framework.response import Response
from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework_api_key.permissions import HasAPIKey
from rest_framework import status
from datetime import timedelta
from django.utils import timezone
from django.db.models import Sum, F, Q
from academics.models import StudentProfile
from learning.models import Course, Enrollment, Lesson, Bookmark
from assessments.models import Test
from gamification.models import Badge, BadgeAward, PointTransaction, Streak

@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def post_login(request):
    # SessionTokenAuthentication guarantees request.user if the session token is valid
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return Response({"detail": "Invalid or missing session token."}, status=status.HTTP_401_UNAUTHORIZED)

    # If users can belong to multiple orgs, pick one (latest active) or return all
    membership = (
        OrganizationMembership.objects
        .filter(user=user)
        .order_by("-id")
        .first()
    )
    if membership is None:
        return Response({"detail": "Organization not found."}, status=status.HTTP_400_BAD_REQUEST)

    if not membership.is_active:
        return Response({"detail": "The user has been deactivated."}, status=status.HTTP_403_FORBIDDEN)
    return Response(
        {
            "detail": "User access granted",
            "org_membership_pk": membership.pk,
            "role": membership.role,
        },
        status=status.HTTP_200_OK,  # <- correct constant
    )




def _fmt_duration(total_seconds: int) -> str:
    if not total_seconds:
        return "0 min"
    mins = int(total_seconds // 60)
    if mins < 60:
        return f"{mins} mins"
    hours = mins / 60.0
    # one decimal place for hours
    return f"{hours:.1f} hours"


def _level_for_xp(xp: int):
    """
    Simple leveling curve. Adjust thresholds/names as you like.
    """
    tiers = [
        (0, "Newbie"),
        (2500, "Bronze Beginner"),
        (5000, "Silver Scholar"),
        (10000, "Gold Graduate"),
        (20000, "Platinum Prodigy"),
    ]
    current = tiers[0]
    next_threshold = None
    for i, (threshold, name) in enumerate(tiers):
        if xp >= threshold:
            current = (threshold, name)
            next_threshold = tiers[i + 1][0] if i + 1 < len(tiers) else None
        else:
            break
    to_next = max((next_threshold - xp), 0) if next_threshold is not None else 0
    # Percent progress within current tier
    floor = current[0]
    span = (next_threshold - floor) if next_threshold is not None else None
    pct = int(((xp - floor) / span) * 100) if span else 100
    return {
        "level_name": current[1],
        "next_threshold": next_threshold,
        "xp_to_next": to_next,
        "progress_to_next_pct": pct,
    }


@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def dashboard_overview(request):
    """
    Returns the dashboard payload for the authenticated user.
    Query params:
      - recent_limit (int, default 3)
      - tests_limit (int, default 4)
    """
    user = request.user
    recent_limit = int(request.query_params.get("recent_limit") or 3)
    tests_limit = int(request.query_params.get("tests_limit") or 4)

    # ---- Organization / Student context ----
    membership = (
        OrganizationMembership.objects
        .filter(user=user, is_active=True)
        .order_by("-id")
        .select_related("organization")
        .first()
    )
    org = membership.organization if membership else None

    student = (
        StudentProfile.objects
        .filter(user=user, organization=org if org else None)
        .select_related("organization", "current_classroom")
        .first()
        if org else
        StudentProfile.objects.filter(user=user).order_by("-id").first()
    )

    # ---- Enrollments for this student ----
    enrollments = Enrollment.objects.none()
    if student:
        enrollments = (
            Enrollment.objects
            .filter(student=student)
            .select_related("course", "course__subject", "course__classroom", "course__teacher")
            .order_by("-id")
        )

    # ---- Course durations (sum lesson durations per course) ----
    # Build a map course_id -> total_seconds
    lesson_qs = Lesson.objects.filter(module__course__in=enrollments.values("course_id")).values(
        "module__course_id"
    ).annotate(total_seconds=Sum("duration_seconds"))
    course_durations = {row["module__course_id"]: (row["total_seconds"] or 0) for row in lesson_qs}

    # ---- Stats: courses, hours, certificates, streak ----
    courses_enrolled = enrollments.count()

    hours_learned = 0.0
    completed_count = 0
    for e in enrollments:
        seconds = course_durations.get(e.course_id, 0)
        hours = (seconds / 3600.0) * (float(e.progress_pct or 0) / 100.0)
        hours_learned += hours
        if (e.progress_pct or 0) >= 100:
            completed_count += 1

    # streak
    streak_days = 0
    if student:
        streak = Streak.objects.filter(student=student).order_by("-id").first()
        streak_days = streak.current_days if streak else 0

    # ---- Gamification: XP & Achievements & Leaderboard ----
    total_xp = 0
    recent_badge_name = None
    unlocked_achievements = 0
    total_achievements = 0
    org_rank = None

    if student:
        total_xp = (
            PointTransaction.objects
            .filter(student=student)
            .aggregate(total=Sum("points"))
            .get("total") or 0
        )

        # Achievements
        unlocked_achievements = BadgeAward.objects.filter(student=student).count()
        if org:
            total_achievements = Badge.objects.filter(organization=org).count()
        recent_badge = BadgeAward.objects.filter(student=student).select_related("badge").order_by("-awarded_at", "-id").first()
        recent_badge_name = recent_badge.badge.name if recent_badge and recent_badge.badge else None

        # Leaderboard rank within org by total points
        if org:
            leaderboard = (
                PointTransaction.objects
                .filter(student__organization=org)
                .values("student_id")
                .annotate(xp=Sum("points"))
                .order_by("-xp")
            )
            # Compute rank by walking the ordered list once
            rank = 1
            for row in leaderboard:
                if row["student_id"] == student.id:
                    org_rank = rank
                    break
                rank += 1

    level_info = _level_for_xp(total_xp)

    # ---- Continue Learning (recent courses) ----
    # Choose latest enrollments and present course + progress + duration + next lesson guess
    recent_courses = []
    if enrollments.exists():
        # Find user's latest bookmark per course (to guess next lesson)
        latest_bookmarks = (
            Bookmark.objects
            .filter(student=student, lesson__module__course__in=enrollments.values("course_id"))
            .select_related("lesson", "lesson__module", "lesson__module__course")
            .order_by("lesson__module__course_id", "-created_at")
        )

        # map course_id -> latest lesson seen
        latest_per_course = {}
        for b in latest_bookmarks:
            cid = b.lesson.module.course_id
            if cid not in latest_per_course:
                latest_per_course[cid] = b.lesson

        for e in enrollments[:recent_limit]:
            c = e.course
            # duration
            total_sec = course_durations.get(c.id, 0)
            duration_label = _fmt_duration(total_sec)
            # next lesson guess
            next_lesson = "Next lesson"
            last_seen = latest_per_course.get(c.id)
            if last_seen:
                # naive guess: the next lesson by order in the same module
                nxt = (
                    Lesson.objects
                    .filter(module=last_seen.module, order__gt=last_seen.order)
                    .order_by("order")
                    .first()
                )
                next_lesson = nxt.name if nxt else last_seen.name
            else:
                # fallback: first lesson in first module
                first = (
                    Lesson.objects
                    .filter(module__course=c)
                    .order_by("module__order", "order")
                    .first()
                )
                next_lesson = first.name if first else "Getting started"

            recent_courses.append({
                "title": c.name,
                "progress": int(e.progress_pct or 0),
                "duration": duration_label,
                "nextLesson": next_lesson,
            })

    # ---- Upcoming Tests ----
    upcoming_tests = []
    if enrollments.exists():
        now = timezone.now()
        tests = (
            Test.objects
            .filter(course_id__in=enrollments.values("course_id"))
            .filter(Q(start_at__isnull=False) & Q(start_at__gte=now))
            .select_related("course")
            .order_by("start_at")[:tests_limit]
        )
        for t in tests:
            upcoming_tests.append({
                "title": t.title,
                "date": t.start_at.isoformat(),
                "duration": f"{t.duration_minutes} mins" if getattr(t, "duration_minutes", None) else None,
                "course": t.course.name if t.course_id else None,
            })

    # ---- Response ----
    payload = {
        "user": {
            "display_name": f"{user.first_name or ''} {user.last_name or ''}".strip() or user.username,
        },
        "stats": {
            "courses_enrolled": courses_enrolled,
            "hours_learned": round(hours_learned, 1),
            "certificates": completed_count,   # treating completed courses as certificates
            "streak_days": streak_days,
        },
        "gamification": {
            "xp": total_xp,
            "level_name": level_info["level_name"],
            "progress_to_next_pct": level_info["progress_to_next_pct"],
            "xp_to_next": level_info["xp_to_next"],
            "achievements": {
                "unlocked": unlocked_achievements,
                "total": total_achievements,
                "recent": recent_badge_name,
            },
            "leaderboard": {
                "org_rank": org_rank,
                # "global_rank": None,  # add if you maintain a global board
            },
        },
        "recent_courses": recent_courses,     # [{title, progress, duration, nextLesson}]
        "upcoming_tests": upcoming_tests,     # [{title, date(ISO), duration, course}]
    }
    return Response(payload, status=status.HTTP_200_OK)
