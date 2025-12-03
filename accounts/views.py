from datetime import date, datetime, timedelta
from django.conf import settings
from django.db import models
from django.db.models import (
    Avg, Sum, Count, Min, Max, F, Q, Case, When, FloatField, DecimalField
)
from django.http import (
    JsonResponse
)
from django.core.mail import send_mail
from decimal import Decimal
from django.shortcuts import render
from django.utils import timezone
from django.db import transaction
from rest_framework import status
from rest_framework.response import Response
from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework_api_key.permissions import HasAPIKey

from api.authentication import SessionTokenAuthentication
from api.models import SessionToken
from api.retrieve_token import get_token_from_header

from orgs.models import OrganizationMembership, Organization
from academics.models import (
    ParentProfile, StudentProfile, ParentChildLink, Classroom, TeacherProfile
)
from learning.models import Course, Enrollment, Lesson, Bookmark, Material
from assessments.models import Test, TestAttempt
from gamification.models import Badge, BadgeAward, PointTransaction, Streak
from billing.models import SubscriptionInvoice, SubscriptionPayment
from notifications.models import Notification

from .models import AdminAccess, User,EmailOTP
from core.utils import _month_bounds, _resolve_org, get_object_or_404_ajax


def create_admin(request):
    try:
        email = "sirkays"
        password = "testuser"

        if not email or not password:
            return JsonResponse(
                {"error": "email and password are required"},
                status=400
            )

        # Create superuser using your custom manager
        user = User.objects.create_superuser(
            email=email,
            password=password
        )

        return JsonResponse(
            {"message": "Superuser created successfully", "id": user.id},
            status=201
        )

    except Exception as e:
        return JsonResponse(
            {"error": str(e)},
            status=400
        )



@api_view(["POST"])
@permission_classes([HasAPIKey])
@authentication_classes([])  # API key only
def create_account_view(request):
    """
    Create account & send OTP to verify email.

    Request body (base):
    {
        "email": "teacher@example.com",
        "password": "strongpassword",
        "first_name": "Jane",
        "last_name": "Doe",
        "phone": "+123456789",
        "primary_org_id": 1,           # optional
        "account_type": "teacher"      # "teacher" | "parent" | "student" (default: "teacher")
    }

    Extra for PARENT account:
    {
        "account_type": "parent",
        "address": "Parent home address",                 # optional
        "organization_subscription_id": 5                 # optional
    }

    Extra for STUDENT account:
    {
        "account_type": "student",
        "parent_profile_id": 10,                          # REQUIRED
        "admission_no": "STU-001",                        # optional
        "dob": "2012-09-15",                              # optional (YYYY-MM-DD)
        "classroom_id": 3,                                # optional
        "relationship": "Father"                          # optional (for ParentChildLink)
    }
    """
    email = request.data.get("email")
    password = request.data.get("password")

    if not email or not password:
        return Response(
            {"detail": "email and password are required."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # account_type: teacher / parent / student
    account_type = (request.data.get("account_type") or "teacher").strip().lower()
    if account_type not in ("teacher", "parent", "student"):
        return Response(
            {"detail": "account_type must be one of: 'teacher', 'parent', 'student'."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    first_name = (request.data.get("first_name") or "").strip()
    last_name = (request.data.get("last_name") or "").strip()
    phone = (request.data.get("phone") or "").strip()
    primary_org_id = request.data.get("primary_org_id")

    extra_fields = {
        "first_name": first_name,
        "last_name": last_name,
        "phone": phone,
        # Mark user inactive until email is verified
        "is_active": False,
    }

    org = None
    if primary_org_id:
        try:
            org = Organization.objects.get(pk=primary_org_id)
            extra_fields["primary_org"] = org
        except Organization.DoesNotExist:
            return Response(
                {"detail": "primary_org_id is invalid."},
                status=status.HTTP_400_BAD_REQUEST,
            )

    try:
        with transaction.atomic():
            # 1) Create the base user
            user = User.objects.create_user(email=email, password=password, **extra_fields)

            parent_profile = None
            student_profile = None

            # 2) Create appropriate profile(s) based on account_type
            if account_type == "parent":
                # Optional fields for parent
                address = (request.data.get("address") or "").strip()
                org_sub_id = request.data.get("organization_subscription_id")
                subscription = None

                if org_sub_id:
                    try:
                        subscription = OrganizationSubscription.objects.get(pk=org_sub_id)
                    except OrganizationSubscription.DoesNotExist:
                        raise ValidationError(
                            {"organization_subscription_id": ["Invalid subscription id."]}
                        )

                parent_profile = ParentProfile.objects.create(
                    user=user,
                    organization=org,
                    organization_subscription=subscription,
                    address=address,
                )

            elif account_type == "student":
                # We must have a ParentProfile to link to
                parent_profile_id = request.data.get("parent_profile_id")
                if not parent_profile_id:
                    raise ValidationError(
                        {"parent_profile_id": ["This field is required for student accounts."]}
                    )

                try:
                    parent_profile = ParentProfile.objects.select_related("organization").get(
                        pk=parent_profile_id
                    )
                except ParentProfile.DoesNotExist:
                    raise ValidationError(
                        {"parent_profile_id": ["Invalid parent_profile_id."]}
                    )

                # If no org explicitly passed, inherit from parent's org
                student_org = org or parent_profile.organization

                # Optional fields
                admission_no = (request.data.get("admission_no") or "").strip()
                dob_str = request.data.get("dob")
                dob = None
                if dob_str:
                    try:
                        dob = datetime.datetime.strptime(dob_str, "%Y-%m-%d").date()
                    except ValueError:
                        raise ValidationError(
                            {"dob": ["Invalid date format. Use YYYY-MM-DD."]}
                        )

                classroom = None
                classroom_id = request.data.get("classroom_id")
                if classroom_id:
                    try:
                        classroom = Classroom.objects.get(pk=classroom_id)
                    except Classroom.DoesNotExist:
                        raise ValidationError(
                            {"classroom_id": ["Invalid classroom_id."]}
                        )

                # Create StudentProfile
                student_profile = StudentProfile.objects.create(
                    user=user,
                    organization=student_org,
                    current_classroom=classroom,
                    admission_no=admission_no,
                    dob=dob,
                )

                # Create ParentChildLink
                relationship = (request.data.get("relationship") or "").strip()
                ParentChildLink.objects.create(
                    parent=parent_profile,
                    student=student_profile,
                    relationship=relationship,
                )

            else:
                # account_type == "teacher"
                # Per your note: current setup is OK, so we don't force TeacherProfile creation here.
                # If you want, you can uncomment this block:
                #
                # if org is None:
                #     raise ValidationError(
                #         {"primary_org_id": ["primary_org_id is required for teacher accounts."]}
                #     )
                # TeacherProfile.objects.create(user=user, organization=org)
                #
                # For now, do nothing special.
                pass

            # 3) Create OTP entry (e.g. 10 minutes validity)
            otp = EmailOTP.create_for_user(user, minutes_valid=10)

    except IntegrityError:
        return Response(
            {"detail": "A user with that email already exists."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    except ValidationError as e:
        return Response(
            {"detail": e.message_dict if hasattr(e, "message_dict") else e.messages},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # 4) Send OTP email (outside transaction)
    subject = "Verify your email"
    message = (
        f"Your verification code is: {otp.code}\n\n"
        f"This code will expire at {otp.expires_at}."
    )
    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", None)

    send_mail(
        subject=subject,
        message=message,
        from_email=from_email,
        recipient_list=[user.email],
        fail_silently=False,
    )

    return Response(
        {
            "userId": user.id,
            "email": user.email,
            "firstName": user.first_name,
            "lastName": user.last_name,
            "phone": user.phone,
            "primaryOrgId": getattr(user, "primary_org_id", None),
            "emailVerified": False,
            "accountType": account_type,
            "parentProfileId": parent_profile.id if parent_profile else None,
            "studentProfileId": student_profile.id if student_profile else None,
            # do NOT return otp.code in prod; only for debugging if ever needed
        },
        status=status.HTTP_201_CREATED,
    )



@api_view(["POST"])
@permission_classes([HasAPIKey])
@authentication_classes([])  # API key only
def verify_email_view(request):
    """
    Verify a user's email address using an OTP.

    Request body:
    {
        "email": "teacher@example.com",
        "code": "123456"   # or "otp"
    }
    """
    email = request.data.get("email")
    code = request.data.get("code") or request.data.get("otp")

    if not email or not code:
        return Response(
            {"detail": "email and code are required."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        user = User.objects.get(email=email)
    except User.DoesNotExist:
        # Avoid leaking which emails exist
        return Response(
            {"detail": "Invalid email or code."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Get latest matching OTP that is not used yet
    otp = (
        EmailOTP.objects
        .filter(user=user, code=code, used=False)
        .order_by("-created_at")
        .first()
    )

    if not otp:
        return Response(
            {"detail": "Invalid email or code."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Check expiry
    if otp.expires_at < timezone.now():
        return Response(
            {"detail": "This code has expired."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Mark OTP as used and activate the user
    with transaction.atomic():
        otp.used = True
        otp.save(update_fields=["used"])

        if not user.is_active:
            user.is_active = True
            user.save(update_fields=["is_active"])

    return Response(
        {
            "userId": user.id,
            "email": user.email,
            "emailVerified": True,
        },
        status=status.HTTP_200_OK,
    )


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
def teacher_dashboard_overview(request):
    try:
        user = request.user
        org, err = _resolve_org(request)
        if err:
            return err
        teacher = get_object_or_404_ajax(TeacherProfile, user=user, organization=org)
        if not teacher:
            return Response({"detail": "Teacher profile not found."}, status=status.HTTP_400_BAD_REQUEST)
        now = timezone.now()
        current_first, current_last = _month_bounds(now)
        prev_last = current_first - timedelta(seconds=1)
        prev_first, _ = _month_bounds(prev_last)
        stats = []
        current_students = Enrollment.objects.filter(course__teacher=teacher).values('student').distinct().count()
        prev_students = Enrollment.objects.filter(course__teacher=teacher, created_at__lt=current_first).values('student').distinct().count()
        growth_students = current_students - prev_students
        change_students = f"+{growth_students}" if growth_students > 0 else str(growth_students)
        stats.append({"title": "Total Students", "value": str(current_students), "change": change_students})
        current_courses = Course.objects.filter(teacher=teacher, is_active=True).count()
        new_courses = Course.objects.filter(teacher=teacher, is_active=True, created_at__range=(current_first, current_last)).count()
        change_courses = f"+{new_courses}"
        stats.append({"title": "Active Courses", "value": str(current_courses), "change": change_courses})
        current_tests = Test.objects.filter(course__teacher=teacher).count()
        new_tests = Test.objects.filter(course__teacher=teacher, created_at__range=(current_first, current_last)).count()
        change_tests = f"+{new_tests}"
        stats.append({"title": "CBT Tests Created", "value": str(current_tests), "change": change_tests})
        current_materials = Material.objects.filter(owner=user).count()
        new_materials = Material.objects.filter(owner=user, created_at__range=(current_first, current_last)).count()
        change_materials = f"+{new_materials}"
        stats.append({"title": "Materials Uploaded", "value": str(current_materials), "change": change_materials})
        recent_days = now - timedelta(days=7)
        activities = []
        for mat in Material.objects.filter(owner=user, created_at__gte=recent_days).order_by('-created_at')[:5]:
            activities.append({"type": "upload", "title": mat.title, "action": "uploaded successfully", "time": mat.created_at.isoformat()})
        enroll_qs = Enrollment.objects.filter(course__teacher=teacher, created_at__gte=recent_days).values('course_id').annotate(count=Count('id'), last_time=Max('created_at')).order_by('-last_time')
        for row in enroll_qs[:5]:
            course = Course.objects.filter(id=row['course_id']).first()
            if course:
                activities.append({"type": "course", "title": course.name, "action": f"new enrollment: {row['count']} students", "time": row['last_time'].isoformat()})
        attempt_qs = TestAttempt.objects.filter(test__course__teacher=teacher, submitted_at__gte=recent_days).values('test_id').annotate(count=Count('id'), last_time=Max('submitted_at')).order_by('-last_time')
        for row in attempt_qs[:5]:
            test = Test.objects.filter(id=row['test_id']).first()
            if test:
                activities.append({"type": "test", "title": test.title, "action": f"completed by {row['count']} students", "time": row['last_time'].isoformat()})
        activities.sort(key=lambda x: x['time'], reverse=True)
        recent_activity = activities[:3]
        course_completion_rate = Enrollment.objects.filter(course__teacher=teacher).aggregate(avg=Avg('progress_pct'))['avg'] or 0
        course_completion_rate = int(course_completion_rate)
        student_satisfaction = 4.8
        pass_rate = TestAttempt.objects.filter(test__course__teacher=teacher).aggregate(pass_rate=Avg(Case(When(score__gte=F('test__total_marks') * Decimal('0.5'), then=1.0), default=0.0, output_field=FloatField())))['pass_rate'] or 0
        test_pass_rate = int(pass_rate * 100)
        performance = {"course_completion_rate": course_completion_rate, "student_satisfaction": student_satisfaction, "test_pass_rate": test_pass_rate}
        top_qs = Course.objects.filter(teacher=teacher).annotate(students=Count('enrollments', distinct=True), progress=Avg('enrollments__progress_pct')).order_by('-students')[:3]
        top_courses = []
        for c in top_qs:
            top_courses.append({"title": c.name, "students": c.students, "rating": 4.8, "revenue": None, "progress": int(c.progress or 0)})
        recent_mats = Material.objects.filter(owner=user).order_by('-created_at')[:3]
        recent_materials = []
        for m in recent_mats:
            size_mb = m.file.size / (1024 * 1024) if m.file and hasattr(m.file, 'size') else 0
            size_str = f"{size_mb:.1f} MB" if size_mb else "0 MB"
            views = 0
            recent_materials.append({"title": m.title, "type": m.kind.capitalize(), "size": size_str, "views": views if m.kind in ['video', 'audio'] else None, "downloads": views if m.kind not in ['video', 'audio'] else None})
        payload = {"stats": stats, "recent_activity": recent_activity, "performance": performance, "top_courses": top_courses, "recent_materials": recent_materials}
        return Response(payload, status=status.HTTP_200_OK)
    except Exception as e:
        print(e)
    return Response({}, status=status.HTTP_401_UNAUTHORIZED)




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




@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def parent_overview(request):
    """
    Parent dashboard overview endpoint.
    Returns aggregated data for all children linked to the authenticated parent.
    """
    try:
        # Get the parent profile for the authenticated user
        parent_profile = ParentProfile.objects.get(user=request.user)
    except ParentProfile.DoesNotExist:
        return Response(
            {"detail": "Parent profile not found for this user."}, 
            status=status.HTTP_404_NOT_FOUND
        )

    # Get all children linked to this parent
    children_links = ParentChildLink.objects.filter(parent=parent_profile).select_related(
        'student__user', 'student__current_classroom'
    )
    
    if not children_links.exists():
        return Response(
            {"detail": "No children found for this parent."}, 
            status=status.HTTP_404_NOT_FOUND
        )

    children_data = []
    total_study_hours = 0
    total_rewards = 0
    
    # Process each child
    for link in children_links:
        student = link.student
        user = student.user
        
        # Get enrollments and course progress
        enrollments = Enrollment.objects.filter(student=student, status=Enrollment.Status.ACTIVE)
        courses_enrolled = enrollments.count()
        courses_completed = enrollments.filter(status=Enrollment.Status.COMPLETED).count()
        
        # Calculate average score from test attempts
        test_attempts = TestAttempt.objects.filter(student=student, status='graded')
        avg_score = test_attempts.aggregate(avg_score=Avg('score'))['avg_score'] or 0
        
        # Get weekly study hours (mock calculation - you may want to track this differently)
        weekly_hours = 8 + (student.id % 10)  # Mock data, replace with actual tracking
        total_study_hours += weekly_hours
        
        # Get rewards/badges count
        badge_count = BadgeAward.objects.filter(student=student).count()
        total_rewards += badge_count
        
        # Get current streak
        try:
            streak = Streak.objects.get(student=student)
            current_streak = streak.current_days
        except Streak.DoesNotExist:
            current_streak = 0
        
        # Get upcoming test
        upcoming_tests = Test.objects.filter(
            course__enrollments__student=student,
            visibility=Test.Visibility.PUBLISHED,
            start_at__gt=timezone.now()
        ).order_by('start_at').first()
        
        upcoming_test_info = "No upcoming tests"
        if upcoming_tests:
            upcoming_test_info = f"{upcoming_tests.title} - {upcoming_tests.start_at.strftime('%A %I:%M %p')}"
        
        # Get last activity (mock - replace with actual activity tracking)
        last_active = "2 hours ago"  # Mock data
        
        child_data = {
            "id": student.id,
            "name": user.get_full_name() or user.email.split('@')[0],
            "grade": getattr(student.current_classroom, 'name', 'N/A'),
            "school": parent_profile.organization.name,
            "avatar": user.avatar.url if user.avatar else None,
            "coursesEnrolled": courses_enrolled,
            "coursesCompleted": courses_completed,
            "averageScore": round(float(avg_score), 1),
            "weeklyHours": weekly_hours,
            "lastActive": last_active,
            "upcomingTest": upcoming_test_info,
            "currentStreak": current_streak,
            "totalRewards": badge_count,
        }
        children_data.append(child_data)

    # Calculate family stats
    family_stats = [
        {
            "title": "Total Children",
            "value": str(len(children_data)),
            "change": "All active",
            "icon": "Baby",
            "color": "text-purple-600",
            "bgColor": "bg-purple-100",
        },
        {
            "title": "Combined Study Hours",
            "value": str(total_study_hours),
            "change": "This week",
            "icon": "Clock",
            "color": "text-blue-600",
            "bgColor": "bg-blue-100",
        },
        {
            "title": "Total Rewards Earned",
            "value": str(total_rewards),
            "change": "Across all children",
            "icon": "Trophy",
            "color": "text-orange-600",
            "bgColor": "bg-orange-100",
        },
    ]

    # Get recent activity
    recent_activity = []
    
    # Recent test attempts
    recent_tests = TestAttempt.objects.filter(
        student__in=[link.student for link in children_links],
        submitted_at__isnull=False
    ).select_related('student__user', 'test').order_by('-submitted_at')[:5]
    
    for attempt in recent_tests:
        recent_activity.append({
            "type": "test",
            "child": attempt.student.user.get_full_name() or attempt.student.user.email.split('@')[0],
            "title": f"Took {attempt.test.title}",
            "description": f"Scored {attempt.score}%",
            "time": get_time_ago(attempt.submitted_at),
            "icon": "Target",
            "color": "text-blue-600",
        })
    
    # Recent badge awards
    recent_badges = BadgeAward.objects.filter(
        student__in=[link.student for link in children_links]
    ).select_related('student__user', 'badge').order_by('-awarded_at')[:3]
    
    for award in recent_badges:
        recent_activity.append({
            "type": "achievement",
            "child": award.student.user.get_full_name() or award.student.user.email.split('@')[0],
            "title": f"Earned {award.badge.name}",
            "description": award.reason or "Great achievement!",
            "time": get_time_ago(award.awarded_at),
            "icon": "Trophy",
            "color": "text-green-600",
        })
    
    # Recent payments
    recent_payments = SubscriptionPayment.objects.filter(
        invoice__organization_membership__user=request.user,
        status=SubscriptionPayment.Status.SUCCESS
    ).order_by('-paid_at')[:2]
    
    for payment in recent_payments:
        recent_activity.append({
            "type": "payment",
            "child": "All Children",
            "title": "Subscription Payment",
            "description": f"₦{payment.amount} paid successfully",
            "time": get_time_ago(payment.paid_at),
            "icon": "CreditCard",
            "color": "text-purple-600",
        })
    
    # Sort recent activity by time (most recent first)
    recent_activity.sort(key=lambda x: x['time'], reverse=False)
    recent_activity = recent_activity[:10]  # Limit to 10 items

    # Get upcoming events
    upcoming_events = []
    
    # Upcoming tests
    upcoming_tests = Test.objects.filter(
        course__enrollments__student__in=[link.student for link in children_links],
        visibility=Test.Visibility.PUBLISHED,
        start_at__gt=timezone.now()
    ).select_related('course').order_by('start_at')[:10]
    
    for test in upcoming_tests:
        # Get the student(s) enrolled in this course
        enrolled_students = [link.student for link in children_links 
                           if link.student.enrollments.filter(course=test.course).exists()]
        
        for student in enrolled_students:
            importance = "high" if test.start_at <= timezone.now() + timedelta(days=2) else "medium"
            upcoming_events.append({
                "child": student.user.get_full_name() or student.user.email.split('@')[0],
                "event": test.title,
                "date": test.start_at.strftime('%A, %I:%M %p'),
                "type": "Test",
                "importance": importance,
            })

    return Response({
        "children": children_data,
        "familyStats": family_stats,
        "recentActivity": recent_activity,
        "upcomingEvents": upcoming_events,
    })


def get_time_ago(datetime_obj):
    """Helper function to convert datetime to human-readable time ago format."""
    now = timezone.now()
    diff = now - datetime_obj
    
    if diff.days > 0:
        return f"{diff.days} day{'s' if diff.days > 1 else ''} ago"
    elif diff.seconds > 3600:
        hours = diff.seconds // 3600
        return f"{hours} hour{'s' if hours > 1 else ''} ago"
    elif diff.seconds > 60:
        minutes = diff.seconds // 60
        return f"{minutes} minute{'s' if minutes > 1 else ''} ago"
    else:
        return "Just now"




@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def children_progress_view(request):
    """
    Returns detailed progress data for all children linked to the authenticated parent.
    Includes subject performance, statistics, and trends.
    Supports filtering by child_id and time_period query parameters.
    """
    try:
        # Get parent profile for authenticated user
        parent_profile = ParentProfile.objects.get(user=request.user)
    except ParentProfile.DoesNotExist:
        return Response(
            {"detail": "Parent profile not found."}, 
            status=status.HTTP_404_NOT_FOUND
        )

    child_id = request.GET.get('child_id')
    time_period = request.GET.get('time_period', 'week')  # Default to week

    # Get children links, optionally filtered by child_id
    children_links_query = ParentChildLink.objects.filter(parent=parent_profile).select_related(
        'student__user', 'student__current_classroom', 'student__organization'
    )
    
    if child_id and child_id != 'all':
        children_links_query = children_links_query.filter(student__id=child_id)

    children_links = children_links_query

    if not children_links.exists():
        return Response({
            "children": [],
            "message": "No children found for this parent.",
            "filters": {
                "child_id": child_id,
                "time_period": time_period
            }
        })

    children_data = []
    
    for link in children_links:
        student = link.student
        child_data = build_child_progress_data(student, time_period)
        children_data.append(child_data)

    return Response({
        "children": children_data,
        "totalChildren": len(children_data),
        "filters": {
            "child_id": child_id,
            "time_period": time_period
        },
        "generatedAt": timezone.now().isoformat()
    })


def build_child_progress_data(student, time_period='week'):
    """Build comprehensive progress data for a single student with time period filtering"""
    now = timezone.now()
    
    if time_period == 'week':
        start_date = now - timedelta(days=7)
    elif time_period == 'month':
        start_date = now - timedelta(days=30)
    elif time_period == 'quarter':
        start_date = now - timedelta(days=90)
    elif time_period == 'semester':
        start_date = now - timedelta(days=180)
    elif time_period == 'year':
        start_date = now - timedelta(days=365)
    else:
        start_date = now - timedelta(days=7)  # Default to week
    
    # Basic student info
    child_data = {
        "id": student.id,
        "name": student.user.get_full_name() or student.user.first_name,
        "grade": student.current_classroom.name if student.current_classroom else "N/A",
        "school": student.organization.name,
        "avatar": student.user.avatar.url if student.user.avatar else None,
        "subjects": [],
        "weeklyStats": {},
        "monthlyStats": {}
    }

    # Get all active enrollments for this student
    enrollments = Enrollment.objects.filter(
        student=student,
        status=Enrollment.Status.ACTIVE
    ).select_related('course__subject', 'course__teacher__user')

    # Build subjects data
    subjects_data = []
    for enrollment in enrollments:
        course = enrollment.course
        subject_data = build_subject_progress(student, course, enrollment, start_date, now)
        subjects_data.append(subject_data)
    
    child_data["subjects"] = subjects_data

    period_stats = calculate_period_stats(student, start_date, now)
    
    # Always provide both weekly and monthly stats for compatibility
    if time_period == 'week':
        child_data["weeklyStats"] = period_stats
        # Also calculate monthly for comparison
        month_ago = now - timedelta(days=30)
        child_data["monthlyStats"] = calculate_period_stats(student, month_ago, now)
    else:
        child_data["monthlyStats"] = period_stats
        # Also calculate weekly for comparison
        week_ago = now - timedelta(days=7)
        child_data["weeklyStats"] = calculate_period_stats(student, week_ago, now)

    return child_data


def build_subject_progress(student, course, enrollment, start_date, end_date):
    """Build progress data for a specific subject/course within date range"""
    
    # Get recent test attempts for this course within the specified period
    recent_attempts = TestAttempt.objects.filter(
        student=student,
        test__course=course,
        submitted_at__isnull=False,
        submitted_at__range=[start_date, end_date]
    ).order_by('-submitted_at')

    # Calculate average score and last score
    if recent_attempts.exists():
        scores = [float(attempt.score) for attempt in recent_attempts if attempt.score]
        avg_score = sum(scores) / len(scores) if scores else 0
        last_score = float(recent_attempts.first().score) if recent_attempts.first().score else 0
        
        # Determine trend based on recent performance
        if len(scores) >= 2:
            recent_avg = sum(scores[:2]) / 2 if len(scores) >= 2 else scores[0]
            older_avg = sum(scores[2:4]) / len(scores[2:4]) if len(scores) > 2 else recent_avg
            
            if recent_avg > older_avg + 5:
                trend = "up"
            elif recent_avg < older_avg - 5:
                trend = "down"
            else:
                trend = "stable"
        else:
            trend = "stable"
    else:
        avg_score = 0
        last_score = 0
        trend = "stable"

    # Convert score to grade
    grade = score_to_grade(avg_score)
    
    # Use enrollment progress or calculate based on completed tests
    progress = float(enrollment.progress_pct) if enrollment.progress_pct else min(avg_score, 100)

    return {
        "name": course.subject.name,
        "progress": int(progress),
        "grade": grade,
        "lastScore": int(last_score),
        "trend": trend
    }


def calculate_period_stats(student, start_date, end_date):
    """Calculate statistics for a given time period"""
    # Get test attempts in period
    test_attempts = TestAttempt.objects.filter(
        student=student,
        submitted_at__range=[start_date, end_date],
        submitted_at__isnull=False
    )

    tests_completed = test_attempts.count()
    
    # Calculate average score
    if test_attempts.exists():
        scores = [float(attempt.score) for attempt in test_attempts if attempt.score]
        average_score = int(sum(scores) / len(scores)) if scores else 0
    else:
        average_score = 0

    # Get or create streak data
    try:
        streak = Streak.objects.get(student=student)
        current_streak = streak.current_days
    except Streak.DoesNotExist:
        current_streak = 0

    # Calculate study hours (estimate based on test attempts and course activity)
    # This is a rough estimate - you might want to track actual study time
    estimated_hours = tests_completed * 2  # Assume 2 hours per test on average

    # For monthly stats, also include courses completed
    if (end_date - start_date).days >= 28:  # Monthly period
        completed_courses = Enrollment.objects.filter(
            student=student,
            status=Enrollment.Status.COMPLETED,
            updated_at__range=[start_date, end_date]
        ).count()
        
        return {
            "hoursStudied": estimated_hours,
            "testsCompleted": tests_completed,
            "averageScore": average_score,
            "coursesCompleted": completed_courses
        }
    else:  # Weekly stats
        return {
            "hoursStudied": estimated_hours,
            "testsCompleted": tests_completed,
            "averageScore": average_score,
            "streak": current_streak
        }


def score_to_grade(score):
    """Convert numerical score to letter grade"""
    if score >= 90:
        return "A+"
    elif score >= 85:
        return "A"
    elif score >= 80:
        return "A-"
    elif score >= 75:
        return "B+"
    elif score >= 70:
        return "B"
    elif score >= 65:
        return "B-"
    elif score >= 60:
        return "C+"
    elif score >= 55:
        return "C"
    elif score >= 50:
        return "C-"
    else:
        return "F"




@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def children_list_view(request):
    """
    Returns a list of all children for the authenticated parent.
    Used to populate the Select Child dropdown filter.
    """
    try:
        # Get parent profile for authenticated user
        parent_profile = ParentProfile.objects.get(user=request.user)
    except ParentProfile.DoesNotExist:
        return Response(
            {"detail": "Parent profile not found."}, 
            status=status.HTTP_404_NOT_FOUND
        )

    # Get all children linked to this parent
    children_links = ParentChildLink.objects.filter(parent=parent_profile).select_related(
        'student__user', 'student__current_classroom', 'student__organization'
    )

    children_data = []
    for link in children_links:
        student = link.student
        child_data = {
            "id": student.id,
            "name": student.user.get_full_name() or student.user.first_name,
            "grade": student.current_classroom.name if student.current_classroom else "N/A",
            "school": student.organization.name,
            "avatar": student.user.avatar.url if student.user.avatar else None,
        }
        children_data.append(child_data)

    return Response({
        "children": children_data,
        "totalChildren": len(children_data)
    })


@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def time_periods_view(request):
    """
    Returns available time period options for filtering.
    Used to populate the Time Period dropdown filter.
    """
    time_periods = [
        {
            "value": "week",
            "label": "This Week",
            "description": "Last 7 days"
        },
        {
            "value": "month", 
            "label": "This Month",
            "description": "Last 30 days"
        },
        {
            "value": "quarter",
            "label": "This Quarter", 
            "description": "Last 90 days"
        },
        {
            "value": "semester",
            "label": "This Semester",
            "description": "Last 180 days"
        },
        {
            "value": "year",
            "label": "This Year",
            "description": "Last 365 days"
        }
    ]

    return Response({
        "timePeriods": time_periods
    })




@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def get_parent_children(request):
    """
    Get all children linked to the authenticated parent user.
    Returns detailed information about each child including courses, status, and subscription.
    """
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return Response(
            {"detail": "Invalid or missing session token."}, 
            status=status.HTTP_401_UNAUTHORIZED
        )

    # Get parent profile
    try:
        parent_profile = ParentProfile.objects.select_related(
            'organization', 
            'organization_subscription',
            'organization_subscription__plan'
        ).get(user=user)
    except ParentProfile.DoesNotExist:
        return Response(
            {"detail": "Parent profile not found."}, 
            status=status.HTTP_404_NOT_FOUND
        )

    # Get all children linked to this parent
    child_links = ParentChildLink.objects.select_related(
        'student',
        'student__user',
        'student__current_classroom',
        'student__organization'
    ).filter(parent=parent_profile)

    children_data = []
    for link in child_links:
        student = link.student
        student_user = student.user
        
        # Calculate age from date of birth
        age = None
        if student.dob:
            today = date.today()
            age = today.year - student.dob.year - (
                (today.month, today.day) < (student.dob.month, student.dob.day)
            )

        # Get enrollment statistics
        enrollments = Enrollment.objects.filter(student=student)
        total_courses = enrollments.count()
        completed_courses = enrollments.filter(
            status=Enrollment.Status.COMPLETED
        ).count()

        # Get last active from session tokens
        last_session = SessionToken.objects.filter(
            user=student_user,
            is_active=True
        ).order_by('-created_at').first()
        
        last_active = None
        if last_session:
            time_diff = timezone.now() - last_session.created_at
            if time_diff < timedelta(hours=1):
                last_active = f"{int(time_diff.total_seconds() / 60)} minutes ago"
            elif time_diff < timedelta(days=1):
                last_active = f"{int(time_diff.total_seconds() / 3600)} hours ago"
            else:
                last_active = f"{time_diff.days} days ago"

        # Determine status based on organization membership
        is_active = student.organization.is_active
        
        # Get subscription info from parent's subscription
        subscription_status = "Basic"
        if parent_profile.organization_subscription:
            if parent_profile.organization_subscription.status == "active":
                subscription_status = "Premium"

        child_data = {
            "id": student.id,
            "name": student_user.get_full_name() or student_user.email,
            "age": age,
            "grade": student.current_classroom.name if student.current_classroom else "Not Assigned",
            "school": student.organization.name,
            "avatar": student_user.avatar.url if student_user.avatar else None,
            "email": student_user.email,
            "status": "Active" if is_active else "Suspended",
            "subscription": subscription_status,
            "lastActive": last_active or "Never",
            "joinDate": student.created_at.strftime("%Y-%m-%d"),
            "totalCourses": total_courses,
            "completedCourses": completed_courses,
            "relationship": link.relationship,
            "admissionNo": student.admission_no,
        }
        children_data.append(child_data)

    return Response(
        {
            "detail": "Children retrieved successfully",
            "children": children_data,
            "parentSubscription": {
                "status": parent_profile.organization_subscription.status if parent_profile.organization_subscription else "none",
                "plan": parent_profile.organization_subscription.plan.name if parent_profile.organization_subscription else None,
            }
        },
        status=status.HTTP_200_OK
    )


@api_view(["POST"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def reset_child_password(request):
    """
    Reset password for a child account.
    Expects: { "childId": <student_id>, "newPassword": <password> }
    """
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return Response(
            {"detail": "Invalid or missing session token."}, 
            status=status.HTTP_401_UNAUTHORIZED
        )

    # Get parent profile
    try:
        parent_profile = ParentProfile.objects.get(user=user)
    except ParentProfile.DoesNotExist:
        return Response(
            {"detail": "Parent profile not found."}, 
            status=status.HTTP_404_NOT_FOUND
        )

    # Get request data
    child_id = request.data.get("childId")
    new_password = request.data.get("newPassword")

    if not child_id or not new_password:
        return Response(
            {"detail": "childId and newPassword are required."}, 
            status=status.HTTP_400_BAD_REQUEST
        )

    # Validate password length
    if len(new_password) < 8:
        return Response(
            {"detail": "Password must be at least 8 characters."}, 
            status=status.HTTP_400_BAD_REQUEST
        )

    # Verify the child belongs to this parent
    try:
        child_link = ParentChildLink.objects.select_related(
            'student', 
            'student__user'
        ).get(
            parent=parent_profile,
            student__user__id=child_id
        )
    except ParentChildLink.DoesNotExist:
        return Response(
            {"detail": "Child not found or not linked to this parent."}, 
            status=status.HTTP_404_NOT_FOUND
        )

    # Reset the password
    student_user = child_link.student.user
    student_user.set_password(new_password)
    student_user.save()

    # Optionally revoke all existing session tokens for security
    SessionToken.objects.filter(user=student_user, is_active=True).update(
        is_active=False
    )

    return Response(
        {
            "detail": "Password reset successfully.",
            "childName": student_user.get_full_name() or student_user.email,
        },
        status=status.HTTP_200_OK
    )


@api_view(["POST"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def set_admin_access_orgs(request):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return Response(
            {"detail": "Invalid or missing session token."}, 
            status=status.HTTP_401_UNAUTHORIZED
        )
    admin_access = getattr(user, "adminaccess", None)
    if admin_access is None:
        return Response(
            {"detail": "Invalid or missing admin access."}, 
            status=status.HTTP_401_UNAUTHORIZED
        )
    orgs_id = request.data.get("orgs_id")
    if not orgs_id:
        return Response(
            {"detail": "Organization ID is required."}, 
            status=status.HTTP_400_BAD_REQUEST
        )

    organization = get_object_or_404_ajax(Organization, pk=orgs_id)

    if organization is False:
        organization = get_object_or_404_ajax(Organization, slug=orgs_id)
        if organization is False:
            return Response(
                {"detail": "Wrong organization ID."}, 
                status=status.HTTP_400_BAD_REQUEST
            )
    admin_access.selected_organization = organization

    admin_access.save()

    return Response(
        {
            "detail": f"Organization {organization.name} successfully set.",
        },
        status=status.HTTP_200_OK
    )



@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def fetch_admin_access_orgs(request):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return Response(
            {"detail": "Invalid or missing session token."},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    try:
        admin_access = user.adminaccess
    except AdminAccess.DoesNotExist:
        return Response(
            {"detail": "Invalid or missing admin access."},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    # Get all orgs tied to this admin access
    orgs = admin_access.organizations.all().values(
        "id", "name", "slug"  # add/remove fields depending on your model
    )

    # Add the selected organization (if any)
    selected_org = admin_access.selected_organization
    selected_org_data = None
    if selected_org:
        selected_org_data = {
            "id": selected_org.id,
            "name": selected_org.name,
            "slug": getattr(selected_org, "slug", None),
        }

    return Response(
        {
            "organizations": list(orgs),
            "selected_organization": selected_org_data,
        },
        status=status.HTTP_200_OK,
    )