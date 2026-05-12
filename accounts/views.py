from datetime import date, datetime, timedelta
from decimal import Decimal
import traceback

# Django core
from django.conf import settings
from django.db import models, transaction, IntegrityError
from django.db.models import (
    Avg, Sum, Count, Min, Max, F, Q, Case, When,
    FloatField, DecimalField, Value
)
from django.db.models.functions import Coalesce
from django.shortcuts import render, get_object_or_404
from django.utils import timezone
from django.http import JsonResponse
from django.core.exceptions import ValidationError, ObjectDoesNotExist
from django.core.mail import send_mail
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.password_validation import (
    validate_password,
    ValidationError as PasswordValidationError
)

# DRF
from rest_framework import status, serializers
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.decorators import (
    api_view,
    permission_classes,
    authentication_classes
)
from rest_framework_api_key.permissions import HasAPIKey

# Local API
from api.authentication import SessionTokenAuthentication
from api.models import SessionToken
from api.retrieve_token import get_token_from_header
from api.permissions import (
    RequiresActiveStudentSubscription,
    APIKeySessionViewSet
)

# Core / Utils
from core.models import Tier
from core.utils import _month_bounds, _resolve_org, get_object_or_404_ajax
from .utils import available_certificates_qs

# Accounts
from .models import AdminAccess, User, EmailOTP, EmailChangeRequest
from .serializers import ResetPasswordSerializer

# Orgs
from orgs.models import OrganizationMembership, Organization

# Academics
from academics.models import (
    ParentProfile, StudentProfile, ParentChildLink,
    Classroom, TeacherProfile, Language, Subject
)

# Learning
from learning.models import Course, Enrollment, Lesson, Bookmark, Material

# Assessments
from assessments.models import Test, TestAttempt

# Gamification
from gamification.models import (
    Badge, BadgeAward, PointTransaction,
    Streak, AchievementDefinition,
    AchievementAcquired, ActivityEvent
)

# Billing
from billing.models import (
    SubscriptionInvoice,
    SubscriptionPayment,
    OrganizationSubscription
)
from billing.services.subscription_invoicing import (
    generate_parent_children_subscription_invoices
)

# Notifications
from notifications.models import Notification
from notifications.services import dispatch
from notifications.events import SYSTEM_WELCOME

# Project settings
from texagonbackend.settings import FRONTEND_ORIGIN

User = get_user_model()

def test_email(request):
    send_mail(
        subject="Verification Test",
        message=f"This email test is success",
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
        recipient_list=["attehkayode2@gmail.com"],
        fail_silently=False,
    )

    return JsonResponse({})

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
def resend_email_otp_view(request):
    """
    Resend OTP to email if user exists and is not active yet.

    Request body:
    { "email": "teacher@example.com" }

    Response is ALWAYS generic to avoid leaking which emails exist.
    """
    email = (request.data.get("email") or "").strip().lower()
    if not email:
        return Response({"detail": "email is required."}, status=status.HTTP_400_BAD_REQUEST)

    # Always return generic success (anti-enumeration)
    generic_ok = Response(
        {"detail": "If this email exists, a new code has been sent."},
        status=status.HTTP_200_OK,
    )

    try:
        user = User.objects.get(email=email)
    except User.DoesNotExist:
        return generic_ok

    # If already verified/active, don't resend
    if user.is_active:
        return generic_ok

    # Simple resend cooldown using last OTP created time
    last_otp = (
        EmailOTP.objects.filter(user=user)
        .order_by("-created_at")
        .first()
    )

    # Optional: 30s cooldown to prevent spam
    COOLDOWN_SECONDS = 30
    if last_otp and (timezone.now() - last_otp.created_at).total_seconds() < COOLDOWN_SECONDS:
        # Still generic, but you can include retry_after for UX
        retry_after = COOLDOWN_SECONDS - int((timezone.now() - last_otp.created_at).total_seconds())
        return Response(
            {"detail": "Please wait before requesting another code.", "retry_after": max(retry_after, 1)},
            status=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    otp = EmailOTP.create_for_user(user, minutes_valid=10)

    try:
        send_mail(
            subject="Verify your email",
            message=f"Your OTP is {otp.code}",
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
            recipient_list=[user.email],
            fail_silently=False,
        )
    except Exception as e:
        return Response(
            {"detail": "Failed to send email", "error": str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    return generic_ok



@api_view(["POST"])
@permission_classes([HasAPIKey])
@authentication_classes([])
def create_account_view(request):
    email = (request.data.get("email") or "").strip().lower()
    password = request.data.get("password")
    account_type = (request.data.get("account_type") or "teacher").strip().lower()

    if not email or not password:
        return Response({"detail": "email and password are required."}, status=400)

    if account_type not in ("teacher", "parent", "student"):
        return Response({"detail": "Invalid account_type"}, status=400)

    # ✅ clean "already exists" check
    existing_user = User.objects.filter(email=email).first()
    if existing_user:
        # 1) active => always in use, no recovery path
        if existing_user.is_active:
            return Response({"detail": "Email already in use."}, status=400)

        # 2) inactive account => let the user resume by resending a fresh OTP
        #    This handles the case where a user started signup but never verified.

        if account_type == "student":
            # Student path: must be linked to the same parent
            parent_profile_id = request.data.get("parent_profile_id")
            if not parent_profile_id:
                return Response({"detail": "parent_profile_id is required"}, status=400)

            student_profile = StudentProfile.objects.filter(user=existing_user).first()
            if not student_profile:
                return Response({"detail": "Email already in use."}, status=400)

            linked = ParentChildLink.objects.filter(
                parent_id=parent_profile_id,
                student=student_profile,
            ).exists()

            if not linked:
                return Response({"detail": "Email already in use."}, status=400)

            # ✅ Same parent + inactive student => resend OTP
            otp = EmailOTP.create_for_user(existing_user, minutes_valid=10)
            try:
                send_mail(
                    subject="Verify your email",
                    message=f"Your OTP is {otp.code}",
                    from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
                    recipient_list=[existing_user.email],
                    fail_silently=False,
                )
            except Exception as e:
                return Response({"detail": "Failed to send email", "error": str(e)}, status=500)

            return Response(
                {
                    "detail": "Account already created. Please verify email with OTP.",
                    "userId": existing_user.id,
                    "email": existing_user.email,
                    "accountType": "student",
                    "studentProfileId": student_profile.id,
                    "existing_inactive": True,
                    "otp_sent": True,
                },
                status=200,
            )

        # ✅ Teacher / Parent inactive => resend OTP so they can continue without admin help
        # Optionally update name/phone in case they mistyped them before abandoning
        existing_user.first_name = request.data.get("first_name", existing_user.first_name)
        existing_user.last_name = request.data.get("last_name", existing_user.last_name)
        existing_user.phone = request.data.get("phone", existing_user.phone)
        existing_user.set_password(request.data.get("password") or "")  # allow them to use a new password
        existing_user.save(update_fields=["first_name", "last_name", "phone", "password"])

        otp = EmailOTP.create_for_user(existing_user, minutes_valid=10)
        try:
            send_mail(
                subject="Verify your email – Techxagon Academy",
                message=(
                    f"Hi {existing_user.first_name or 'there'},\n\n"
                    f"You started creating an account but didn't finish verifying your email.\n"
                    f"Your new verification code is: {otp.code}\n\n"
                    f"This code expires in 10 minutes.\n\n"
                    f"If you did not request this, please ignore this email."
                ),
                from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
                recipient_list=[existing_user.email],
                fail_silently=False,
            )
        except Exception as e:
            return Response({"detail": "Failed to send email", "error": str(e)}, status=500)

        # Determine profile id to return
        parent_profile = getattr(existing_user, "parent_profile", None)
        teacher_profile = getattr(existing_user, "teacher_profile", None)

        return Response(
            {
                "detail": "Account already created. A new verification code has been sent to your email.",
                "userId": existing_user.id,
                "email": existing_user.email,
                "accountType": account_type,
                "parentProfileId": parent_profile.id if parent_profile else None,
                "teacherProfileId": teacher_profile.id if teacher_profile else None,
                "existing_inactive": True,   # ✅ frontend uses this to skip to OTP step
                "otp_sent": True,
            },
            status=200,
        )


    user = None
    parent_profile = None
    student_profile = None
    otp = None

    try:
        with transaction.atomic():
            # Create user (inactive until OTP verification)
            user = User.objects.create_user(
                email=email,
                password=password,
                first_name=request.data.get("first_name", ""),
                last_name=request.data.get("last_name", ""),
                phone=request.data.get("phone", ""),
                is_active=False,
            )

            if account_type == "parent":
                parent_profile = ParentProfile.objects.create(
                    user=user,
                    address=request.data.get("address", ""),
                )

            elif account_type == "student":
                parent_profile_id = request.data.get("parent_profile_id")

                if not parent_profile_id:
                    # raising triggers atomic rollback
                    raise ValidationError({"detail": "parent_profile_id is required"})

                try:
                    parent_profile = ParentProfile.objects.get(pk=parent_profile_id)
                except ParentProfile.DoesNotExist:
                    raise ValidationError({"detail": "Invalid parent_profile_id"})

                dob = request.data.get("dob")
                student_profile = StudentProfile.objects.create(
                    user=user,
                    admission_no=request.data.get("admission_no", ""),
                    dob=dob,  # ✅ add this (ensure StudentProfile has dob field)
                )


                ParentChildLink.objects.create(
                    parent=parent_profile,
                    student=student_profile,
                    relationship=request.data.get("relationship", ""),
                )

            # Create OTP inside transaction
            otp = EmailOTP.create_for_user(user, minutes_valid=10)

        # Send OTP email after transaction is committed
        try:
            send_mail(
                subject="Verify your email",
                message=f"Your OTP is {otp.code}",
                from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
                recipient_list=[user.email],
                fail_silently=False,
            )
        except Exception as e:
            print(e)
            return Response(
                {"detail": "Failed to send email", "error": str(e)},
                status=500,
            )

        return Response(
            {
                "userId": user.id,
                "email": user.email,
                "accountType": account_type,
                "parentProfileId": parent_profile.id if parent_profile else None,
                "studentProfileId": student_profile.id if student_profile else None,
            },
            status=201,
        )

    except IntegrityError:
        # Fallback safety (race conditions / DB uniqueness)
        return Response({"detail": "A user with this email already exists."}, status=400)

    except ValidationError as e:
        if hasattr(e, "message_dict"):
            # flatten common shapes
            detail = e.message_dict.get("detail") or e.message_dict
            return Response({"detail": detail}, status=400)
        return Response({"detail": str(e)}, status=400)

    except Exception as e:
        return Response({"detail": "Server error", "error": str(e)}, status=500)



@api_view(["POST"])
@permission_classes([HasAPIKey])
@authentication_classes([])
def resume_parent_flow_view(request):
    try:
        email = (request.data.get("email") or "").strip().lower()
        password = request.data.get("password") or ""

        if not email or not password:
            return Response({"detail": "email and password are required."}, status=400)

        # Authenticate using email (your User.USERNAME_FIELD = "email")
        user = authenticate(request, email=email, password=password)

        # Avoid leaking whether email exists
        if not user:
            return Response({"detail": "Invalid credentials."}, status=400)

        # If you require verified email before resuming:
        if not user.is_active:
            return Response(
                {"detail": "Email not verified. Please verify your email first."},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Must be a parent account
        try:
            parent_profile = user.parent_profile
        except Exception:
            return Response({"detail": "This account is not a parent account."}, status=400)

        return Response(
            {
                "userId": user.id,
                "email": user.email,
                "accountType": "parent",
                "parentProfileId": parent_profile.id,
                "studentProfileId": None,
            },
            status=200,
        )

    except Exception as e:
        print(e)
    return Response({"detail": "An error occured"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
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
    email = (request.data.get("email") or "").strip().lower()
    code = (request.data.get("code") or request.data.get("otp") or "").strip()

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
            dispatch(
                users=[user],
                message=SYSTEM_WELCOME,
                data={
                    "cta": {
                        "label": "Sign In",
                        "url": f"{FRONTEND_ORIGIN}/login/",
                    }
                },
                ctx={"app_name": "Techxagon Academy"},
                send_in_app=True,
                send_email=True,
                fail_silently=True, 
            )
        try:
            if request.user != user:
                student_profile = get_object_or_404_ajax(StudentProfile, user=user)
                if student_profile and getattr(request.user, "parent_profile"):
                    parent_profile = request.user.parent_profile
                    if getattr(parent_profile, "organization") and request.user.primary_org:
                        student_profile.organization =  request.user.primary_org
                        student_profile.save()

                        def _after_commit():
                            if getattr(settings, "USE_CELERY", False):
                                from billing.tasks import generate_invoices_for_org
                                generate_invoices_for_org.delay(request.user.primary_org.id)
                            else:
                                generate_parent_children_subscription_invoices(
                                    org_id=request.user.primary_org.id,
                                    now=timezone.now(),
                                    dry_run=False,
                                    user=request.user,
                                )

                        transaction.on_commit(_after_commit)

        except Exception as e:
            print(e)

    return Response(
        {
            "userId": user.id,
            "email": user.email,
            "emailVerified": True,
        },
        status=status.HTTP_200_OK,
    )


@api_view(["POST"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])  # API key only
def verify_email_view_authenticated(request):
    """
    Verify a user's email address using an OTP.

    Request body:
    {
        "email": "teacher@example.com",
        "code": "123456"   # or "otp"
    }
    """
    email = (request.data.get("email") or "").strip().lower()
    code = (request.data.get("code") or request.data.get("otp") or "").strip()

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

    # # Mark OTP as used and activate the user
    with transaction.atomic():
        otp.used = True
        otp.save(update_fields=["used"])

        if not user.is_active:
            user.is_active = True
            user.save(update_fields=["is_active"])
    try:
        if request.user != user:
            student_profile = get_object_or_404_ajax(StudentProfile, user=user)
            if student_profile and getattr(request.user, "parent_profile"):
                parent_profile = request.user.parent_profile
                if getattr(parent_profile, "organization") and request.user.primary_org:
                    student_profile.organization =  request.user.primary_org
                    student_profile.save()

                    user.primary_org = request.user.primary_org
                    user.save()

                    OrganizationMembership.objects.get_or_create(
                        user=user,
                        organization= request.user.primary_org,
                        role="student",

                    )
                    generate_parent_children_subscription_invoices(
                        org_id=request.user.primary_org.id,
                        now=timezone.now(),
                        dry_run=False,
                        user=request.user,
                    )
    except Exception as e:
        print(e)

    return Response(
        {
            "userId": user.id,
            "email": user.email,
            "emailVerified": True,
        },
        status=status.HTTP_200_OK,
    )



@api_view(["POST"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def fetch_user_detail(request):
    try:
        email = request.data.get("email")

        if not email:
            return Response(
                {
                    "success": False,
                    "error": "VALIDATION_ERROR",
                    "detail": "Email field is required.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            user = User.objects.select_related("primary_org").get(email__iexact=email)
        except User.DoesNotExist:
            return Response(
                {
                    "success": False,
                    "error": "NOT_FOUND",
                    "detail": "User with this email does not exist.",
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        # ---- Safe avatar handling ----
        avatar_url = None
        try:
            # user.avatar may exist but have no file
            if getattr(user, "avatar", None) and getattr(user.avatar, "name", ""):
                avatar_url = user.avatar.url
        except Exception:
            avatar_url = None  # swallow any avatar-related error

        is_activated = None

        if user.primary_org_id and user.memberships.filter(is_active=True).exists():
            is_activated = user.primary_org_id



        # Base user payload
        data = {
            "success": True,
            "id": user.id,
            "email": user.email,
            "full_name": user.get_full_name(),
            "first_name": user.first_name,
            "last_name": user.last_name,
            "phone": user.phone,
            "avatar": avatar_url,
            "primary_org_id": is_activated,
            "is_active": user.is_active,
            "is_staff": user.is_staff,
            "profile_type": "user",  # will be overridden below if a profile exists
        }

        # ---- Teacher profile ----
        if hasattr(user, "teacher_profile"):
            tp = user.teacher_profile
            data["profile_type"] = "teacher"
            data["teacher_profile"] = {
                "id": tp.id,
                "organization_id": tp.organization_id,
                "bio": tp.bio,
                "experience": tp.experience,
                "languages": list(tp.languages.values("id", "language_name")),
                "specialties": list(tp.specialties.values("id", "name")),
            }
            return Response(data, status=status.HTTP_200_OK)

        # ---- Student profile (with parent links) ----
        if hasattr(user, "student_profile"):
            sp = user.student_profile
            parent_links_qs = sp.parent_links.select_related("parent__user")

            parent_links = []
            for link in parent_links_qs:
                parent_profile = link.parent
                parent_user = parent_profile.user
                parent_links.append(
                    {
                        "link_id": link.id,
                        "relationship": link.relationship,
                        "parent_profile_id": parent_profile.id,
                        "parent_user": {
                            "id": parent_user.id,
                            "email": parent_user.email,
                            "full_name": parent_user.get_full_name(),
                        },
                    }
                )

            data["profile_type"] = "student"
            data["student_profile"] = {
                "id": sp.id,
                "organization_id": sp.organization_id,
                "current_classroom_id": sp.current_classroom_id,
                "admission_no": sp.admission_no,
                "dob": sp.dob,
                "parent_links": parent_links,
            }
            return Response(data, status=status.HTTP_200_OK)

        # ---- Parent profile (with child links) ----
        if hasattr(user, "parent_profile"):
            pp = user.parent_profile
            children_links_qs = pp.children_links.select_related(
                "student__user", "student__current_classroom"
            )

            child_links = []
            for link in children_links_qs:
                student_profile = link.student
                student_user = student_profile.user
                child_links.append(
                    {
                        "link_id": link.id,
                        "relationship": link.relationship,
                        "student_profile_id": student_profile.id,
                        "student_user": {
                            "id": student_user.id,
                            "email": student_user.email,
                            "full_name": student_user.get_full_name(),
                        },
                        "admission_no": student_profile.admission_no,
                        "current_classroom_id": student_profile.current_classroom_id,
                    }
                )

            data["profile_type"] = "parent"
            data["parent_profile"] = {
                "id": pp.id,
                "organization_id": pp.organization_id,
                "organization_subscription_id": pp.organization_subscription_id,
                "address": pp.address,
                "last_billed_at": pp.last_billed_at,
                "children_links": child_links,
            }
            return Response(data, status=status.HTTP_200_OK)

        # ---- No profile (just User model) ----
        return Response(data, status=status.HTTP_200_OK)

    except Exception as e:
        print("Fetch User API Failed")
        return Response(
            {
                "success": False,
                "error": "SERVER_ERROR",
                "detail": str(e),
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )




@api_view(["POST"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def verify_and_update_user(request):
    """
    Body example:

    {
      "email": "student@example.com",
      "profile": {
        "address": "New address",        # ParentProfile
        "dob": "2010-01-01",             # StudentProfile
        "bio": "New bio",                # TeacherProfile
        "experience": 5
      }
    }
    """

    email = request.data.get("email")
    profile_payload = request.data.get("profile", {}) or {}

    if not email:
        return Response(
            {"detail": "Email field is required."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # 1) Ensure the caller has AdminAccess and a selected_organization
    try:
        admin_access = AdminAccess.objects.select_related("selected_organization").get(
            user=request.user,
            active=True,
        )
    except AdminAccess.DoesNotExist:
        return Response(
            {"detail": "You do not have AdminAccess."},
            status=status.HTTP_403_FORBIDDEN,
        )

    organization = admin_access.selected_organization
    if not organization:
        return Response(
            {"detail": "No selected_organization set on your AdminAccess."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # 2) Find the target user (case-insensitive email)
    try:
        user = User.objects.get(email__iexact=email)
    except User.DoesNotExist:
        return Response(
            {"detail": "User with this email does not exist."},
            status=status.HTTP_404_NOT_FOUND,
        )

    # 3) Determine which profile this user has
    profile = None
    profile_type = None


    if hasattr(user, "parent_profile"):
        profile = user.parent_profile
        profile_type = "parent"
    elif hasattr(user, "teacher_profile"):
        profile = user.teacher_profile
        profile_type = "teacher"
    elif hasattr(user, "student_profile"):
        profile = user.student_profile
        profile_type = "student"
    elif hasattr(user, "adminaccess") is False:
        profile, stat = TeacherProfile.objects.get_or_create(user=user,organization=organization)
        profile_type = "teacher"
    else:
        return Response(
            {"detail": "User does not have a Parent, Teacher, or Student profile."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Attach organization
    profile.organization = organization
    OrganizationMembership.objects.get_or_create(
        user=user,
        organization=organization,
        role=profile_type,
    )

    org_sub = OrganizationSubscription.get_lastest_org_sub(organization)

    profile.organization_subscription = org_sub

    if not user.primary_org:
        user.primary_org = organization
        user.save()

    # Allowed fields
    if profile_type == "parent":
        allowed_fields = {"address", "last_billed_at"}
    elif profile_type == "teacher":
        allowed_fields = {"bio", "experience"}

        # 🔹 LANGUAGES (ManyToMany)
        language_ids = profile_payload.get("language_ids")
        if language_ids is not None:
            qs = Language.objects.filter(id__in=language_ids)
            profile.languages.set(qs)

        # 🔹 SPECIALTIES (ManyToMany)
        specialty_ids = profile_payload.get("specialty_ids")
        if specialty_ids is not None:
            qs = Subject.objects.filter(
                id__in=specialty_ids,
                organization=organization  # enforce same org
            )
            profile.specialties.set(qs)
    else:  # student
        allowed_fields = {"admission_no", "dob"}

        # 🔹 handle classroom specifically
        classroom_id = profile_payload.get("current_classroom_id")
        if classroom_id is not None:
            try:
                classroom = Classroom.objects.get(
                    id=classroom_id,
                    organization=organization,  # ensure classroom belongs to same org
                )
            except Classroom.DoesNotExist:
                return Response(
                    {"detail": "Classroom not found in your selected organization."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            profile.current_classroom = classroom

    # Update simple fields
    for field, value in profile_payload.items():
        if field in allowed_fields:
            setattr(profile, field, value)

    profile.save()


    # 6) Simple response (you can serialize the profile if you have serializers)
    data = {
        "user_id": user.id,
        "email": user.email,
        "profile_type": profile_type,
        "organization_id": organization.id,
        "organization_name": getattr(organization, "name", None),
    }

    return Response(data, status=status.HTTP_200_OK)




@api_view(["POST"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def update_parent_child_link(request):
    """
    Update ParentChildLink by creating, updating or deleting using an email of parent or student.
    Request body:
    {
        "email": "parent_or_student@example.com",
        "other_email": "the_other@example.com",
        "action": "create", "update" or "delete",
        "relationship": "optional_relationship"  # required for update, optional for create
    }
    """
    try:
        admin_access = AdminAccess.objects.select_related("selected_organization").get(
            user=request.user,
            active=True,
        )
    except AdminAccess.DoesNotExist:
        return Response(
            {"detail": "You do not have AdminAccess."},
            status=status.HTTP_403_FORBIDDEN,
        )
        
    email = request.data.get("email")
    other_email = request.data.get("other_email")
    action = request.data.get("action")
    relationship = request.data.get("relationship")

    if not email or not other_email or not action:
        return Response(
            {"detail": "email, other_email, and action are required."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if action not in ["create", "update", "delete"]:
        return Response(
            {"detail": "action must be 'create', 'update' or 'delete'."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Get user for email
    try:
        user = User.objects.get(email=email)
    except User.DoesNotExist:
        return Response(
            {"detail": "Invalid email."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Get profiles for email
    parent = ParentProfile.objects.filter(user=user).first()
    student = StudentProfile.objects.filter(user=user).first()
    if not parent and not student:
        return Response(
            {"detail": "No parent or student profile found for the email."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Get other user and profile
    try:
        other_user = User.objects.get(email=other_email)
    except User.DoesNotExist:
        return Response(
            {"detail": "Invalid other_email."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if parent:
        other_profile = StudentProfile.objects.filter(user=other_user).first()
        if not other_profile:
            return Response(
                {"detail": "other_email does not correspond to a student."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        link_parent = parent
        link_student = other_profile
    else:
        other_profile = ParentProfile.objects.filter(user=other_user).first()
        if not other_profile:
            return Response(
                {"detail": "other_email does not correspond to a parent."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        link_parent = other_profile
        link_student = student

    # Check same organization
    if link_parent.organization != link_student.organization:
        return Response(
            {"detail": "Parent and student must belong to the same organization."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    with transaction.atomic():
        if action == "create":
            # Check if student is already linked to another parent
            if link_student.parent_links.exclude(parent=link_parent).exists():
                return Response(
                    {"detail": "This student is already linked to another parent."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            # Check if link already exists
            if ParentChildLink.objects.filter(parent=link_parent, student=link_student).exists():
                return Response(
                    {"detail": "Link already exists."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            ParentChildLink.objects.create(
                parent=link_parent,
                student=link_student,
                relationship=relationship or "",
            )
        elif action == "update":
            if not relationship:
                return Response(
                    {"detail": "relationship is required for update."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            try:
                link = ParentChildLink.objects.get(parent=link_parent, student=link_student)
                link.relationship = relationship
                link.save(update_fields=["relationship"])
            except ParentChildLink.DoesNotExist:
                return Response(
                    {"detail": "Link does not exist."},
                    status=status.HTTP_404_NOT_FOUND,
                )
        elif action == "delete":
            ParentChildLink.objects.filter(parent=link_parent, student=link_student).delete()

    # Get all links attached to the email
    if parent:
        links_qs = parent.children_links.all()
        links = [
            {
                "student_email": link.student.user.email,
                "relationship": link.relationship,
            }
            for link in links_qs
        ]
    else:
        links_qs = student.parent_links.all()
        links = [
            {
                "parent_email": link.parent.user.email,
                "relationship": link.relationship,
            }
            for link in links_qs
        ]

    return Response(
        {
            "email": email,
            "links": links,
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

    user.last_login = timezone.now()
    user.save()
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
    # Check if this user also has active AdminAccess
    has_admin_access = AdminAccess.user_has_admin_access(user)

    return Response(
        {
            "detail": "User access granted",
            "org_membership_pk": membership.pk,
            "role": membership.role,
            "is_generated": user.is_generated,
            "has_admin_access": has_admin_access,
        },
        status=status.HTTP_200_OK,
    )


@api_view(["POST"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def verify_password(request):
    """
    Verify the current authenticated user's password.
    Used as a security gate before allowing dashboard-role switching.

    Body: { "password": "user_plaintext_password" }
    Returns: { "valid": true/false }
    """
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return Response(
            {"detail": "Authentication required."},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    password = request.data.get("password")
    if not password:
        return Response(
            {"detail": "Password is required."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # check_password works on the AbstractBaseUser
    if user.check_password(password):
        return Response({"valid": True}, status=status.HTTP_200_OK)
    else:
        return Response({"valid": False, "detail": "Incorrect password."}, status=status.HTTP_200_OK)


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
    Leveling curve backed by Tier model.
    """
    xp = max(int(xp or 0), 0)

    # Current tier = highest threshold <= xp
    current = (
        Tier.objects
        .filter(threshold_xp__lte=xp)
        .order_by("-threshold_xp")
        .first()
    )

    if not current:
        return {
            "level_name": "Newbie",
            "next_threshold": None,
            "xp_to_next": 0,
            "progress_to_next_pct": 100,
        }

    # Next tier = smallest threshold > current threshold
    next_tier = (
        Tier.objects
        .filter(threshold_xp__gt=current.threshold_xp)
        .order_by("threshold_xp")
        .first()
    )

    next_threshold = next_tier.threshold_xp if next_tier else None
    xp_to_next = max(next_threshold - xp, 0) if next_threshold else 0

    floor = current.threshold_xp
    if next_threshold:
        span = max(next_threshold - floor, 1)
        pct = int(((xp - floor) / span) * 100)
    else:
        pct = 100

    return {
        "level_name": current.name,
        "next_threshold": next_threshold,
        "xp_to_next": xp_to_next,
        "progress_to_next_pct": pct,
    }



@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def teacher_dashboard_overview(request):
    def format_datetime(dt):
        return timezone.localtime(dt).strftime("%b %d, %Y · %I:%M %p")
        
    def safe_file_size_mb(file_field):
        if not file_field:
            return None

        try:
            size = getattr(file_field, "size", None)
            if size is None:
                return None
            return round(size / (1024 * 1024), 1)
        except (OSError, FileNotFoundError, ValueError, TypeError):
                return None

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
        recent_days = now - timedelta(days=7)

        # ---------- STATS ----------
        stats = []

        current_students = (
            Enrollment.objects.filter(course__teacher=teacher)
            .values("student").distinct().count()
        )
        prev_students = (
            Enrollment.objects.filter(course__teacher=teacher, created_at__lt=current_first)
            .values("student").distinct().count()
        )
        growth_students = current_students - prev_students
        change_students = f"+{growth_students}" if growth_students > 0 else str(growth_students)
        stats.append({"title": "Total Students", "value": str(current_students), "change": change_students})

        current_courses = Course.objects.filter(teacher=teacher, is_active=True).count()
        new_courses = Course.objects.filter(
            teacher=teacher, is_active=True, created_at__range=(current_first, current_last)
        ).count()
        stats.append({"title": "Active Courses", "value": str(current_courses), "change": f"+{new_courses}"})

        current_tests = Test.objects.filter(course__teacher=teacher).count()
        new_tests = Test.objects.filter(
            course__teacher=teacher, created_at__range=(current_first, current_last)
        ).count()
        stats.append({"title": "CBT Tests Created", "value": str(current_tests), "change": f"+{new_tests}"})

        # ✅ CONTENT UPLOADED now comes from Lesson (instead of Material)
        # If Lesson doesn't have created_at, replace created_at with whatever timestamp field exists.
        current_content = Lesson.objects.filter(module__course__teacher=teacher).count()
        new_content = Lesson.objects.filter(
            module__course__teacher=teacher,
            created_at__range=(current_first, current_last),
        ).count()
        stats.append({"title": "Content Uploaded", "value": str(current_content), "change": f"+{new_content}"})

        # ---------- RECENT ACTIVITY ----------
        activities = []

        # ✅ Lesson “uploads” (new content created)
        recent_lessons = (
            Lesson.objects.filter(module__course__teacher=teacher, created_at__gte=recent_days)
            .select_related("module__course")
            .order_by("-created_at")[:5]
        )
        for lesson in recent_lessons:
            course_name = ""
            try:
                course_name = lesson.module.course.name
            except Exception:
                pass

            activities.append({
                "type": "upload",
                "title": lesson.name,  # Lesson inherits NamedModel => name field
                "action": f"added new content{f' in {course_name}' if course_name else ''}",
                "time": format_datetime(lesson.created_at),
                "_dt": lesson.created_at,  # internal sort key
            })

        # enrollments (unchanged)
        enroll_qs = (
            Enrollment.objects.filter(course__teacher=teacher, created_at__gte=recent_days)
            .values("course_id")
            .annotate(count=Count("id"), last_time=Max("created_at"))
            .order_by("-last_time")
        )
        for row in enroll_qs[:5]:
            course = Course.objects.filter(id=row["course_id"]).first()
            if course and row["last_time"]:
                activities.append({
                    "type": "course",
                    "title": course.name,
                    "action": f"new enrollment: {row['count']} students",
                    "time": format_datetime(row["last_time"]),
                    "_dt": row["last_time"],
                })

        # attempts (unchanged)
        attempt_qs = (
            TestAttempt.objects.filter(test__course__teacher=teacher, submitted_at__gte=recent_days)
            .values("test_id")
            .annotate(count=Count("id"), last_time=Max("submitted_at"))
            .order_by("-last_time")
        )
        for row in attempt_qs[:5]:
            test = Test.objects.filter(id=row["test_id"]).first()
            if test and row["last_time"]:
                activities.append({
                    "type": "test",
                    "title": test.title,
                    "action": f"completed by {row['count']} students",
                    "time": format_datetime(row["last_time"]),
                    "_dt": row["last_time"],
                })

        # sort by actual datetime, not formatted string
        activities.sort(key=lambda x: x.get("_dt") or timezone.make_aware(timezone.datetime.min), reverse=True)
        for a in activities:
            a.pop("_dt", None)

        recent_activity = activities[:3]

        # ---------- PERFORMANCE ----------
        course_completion_rate = (
            Enrollment.objects.filter(course__teacher=teacher)
            .aggregate(avg=Avg("progress_pct"))["avg"] or 0
        )
        course_completion_rate = int(course_completion_rate)

        student_satisfaction = 4.8

        pass_rate = (
            TestAttempt.objects.filter(test__course__teacher=teacher)
            .aggregate(
                pass_rate=Avg(
                    Case(
                        When(score__gte=F("test__total_marks") * Decimal("0.5"), then=1.0),
                        default=0.0,
                        output_field=FloatField(),
                    )
                )
            )["pass_rate"] or 0
        )
        test_pass_rate = int(pass_rate * 100)

        performance = {
            "course_completion_rate": course_completion_rate,
            "student_satisfaction": student_satisfaction,
            "test_pass_rate": test_pass_rate,
        }
        # ---------- TOP COURSES ----------
        top_qs = (
            Course.objects.filter(teacher=teacher)
            .annotate(
                students=Count("enrollments", distinct=True),
                progress=Avg("enrollments__progress_pct"),
            )
            .order_by("-students")[:3]
        )
        top_courses = []
        for c in top_qs:
            top_courses.append({
                "title": c.name,
                "students": c.students,
                "rating": 4.8,
                "revenue": None,
                "progress": int(c.progress or 0),
            })

        # ---------- RECENT CONTENT (LESSONS) ----------
        recent_lessons = (
            Lesson.objects.filter(module__course__teacher=teacher)
            .select_related("module__course")
            .order_by("-created_at")[:3]
        )

        recent_materials = []

        for l in recent_lessons:
            size_mb = safe_file_size_mb(getattr(l, "file", None))
            size_str = f"{size_mb} MB" if size_mb is not None else "Unknown"
            views = 0  # placeholder

            recent_materials.append({
                "title": l.name,
                "type": l.content_type.capitalize() if l.content_type else "Content",
                "size": size_str,
                "views": views if l.content_type in ["video", "audio"] else None,
                "downloads": views if l.content_type not in ["video", "audio"] else None,
            })


        payload = {
            "stats": stats,
            "recent_activity": recent_activity,
            "performance": performance,
            "top_courses": top_courses,
            "recent_materials": recent_materials,
        }
        return Response(payload, status=status.HTTP_200_OK)

    except Exception as e:
        print(e)
        return Response({"detail": "Server error"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["GET"])
@permission_classes([HasAPIKey & RequiresActiveStudentSubscription()])
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
            .filter(student=student,  status=Enrollment.Status.ACTIVE)
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

    completed_count = available_certificates_qs().filter(student=student).count()

    # streak
    streak_days = 0
    if student:
        streak = Streak.objects.filter(student=student).order_by("-last_activity").first()
        streak_days = streak.current_days if streak else 0

    # ---- Gamification: XP & Achievements & Leaderboard ----
    total_xp = 0
    recent_badge_name = None
    unlocked_achievements = 0
    total_achievements = 0
    org_rank = None
    global_rank = None
    if student:
        total_xp = (
            PointTransaction.objects
            .filter(student=student)
            .aggregate(total=Sum("points"))
            .get("total") or 0
        )

        # Achievements
        unlocked_achievements = AchievementAcquired.objects.filter(student=student).count()
        if org:
            total_achievements = AchievementDefinition.objects.filter(is_active=True).count()
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


        leaderboard = (
            PointTransaction.objects
            .all()
            .values("student_id")
            .annotate(xp=Sum("points"))
            .order_by("-xp")
        )
        # Compute rank by walking the ordered list once
        rank = 1
        for row in leaderboard:
            if row["student_id"] == student.id:
                global_rank = rank
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
    badges = BadgeAward.objects.filter(student=student).count()
    # ---- Response ----
    payload = {
        "user": {
            "display_name": f"{user.first_name or ''} {user.last_name or ''}".strip() or user.username,
        },
        "stats": {
            "courses_enrolled": courses_enrolled,
            "badges_earned": badges,
            "certificates": completed_count,  
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
                "global_rank": global_rank,  # add if you maintain a global board
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
        try:
            print(request.user, " scndjcndkjcndj ")
            parent_profile = ParentProfile.objects.select_related("organization").get(user=request.user)
        except ParentProfile.DoesNotExist:
            return Response(
                {"detail": "Parent profile not found for this user."},
                status=status.HTTP_404_NOT_FOUND,
            )
        print(parent_profile, " end...")

        # Get all children linked to this parent
        children_links = (
            ParentChildLink.objects
            .filter(parent=parent_profile)
            .select_related("student__user", "student__current_classroom")
        )

        print(children_links, " neeeeeee")

        if not children_links.exists():
            return Response(
                {"detail": "No children found for this parent."},
                status=status.HTTP_404_NOT_FOUND,
            )

        students = [link.student for link in children_links]
        org = parent_profile.organization

        # --- Precompute / batch queries for accuracy & performance ---

        # Enrollment counts per student
        enrollments_qs = Enrollment.objects.filter(student__in=students)
        active_counts = (
            enrollments_qs.filter(status=Enrollment.Status.ACTIVE,
            course__course_type="public"
            )
            .values("student_id")
            .annotate(c=Count("id"))
        )
        completed_counts = (
            enrollments_qs.filter(status=Enrollment.Status.COMPLETED,
            course__course_type="public"
            )
            .values("student_id")
            .annotate(c=Count("id"))
        )

        active_map = {r["student_id"]: r["c"] for r in active_counts}
        completed_map = {r["student_id"]: r["c"] for r in completed_counts}

        # Average test score per student (graded)
        avg_scores = (
            TestAttempt.objects.filter(student__in=students, status="submitted")
            .values("student_id")
            .annotate(avg=Avg("score"))
        )
        avg_score_map = {r["student_id"]: (r["avg"] or 0) for r in avg_scores}

        # Badges count per student
        badge_counts = (
            BadgeAward.objects.filter(student__in=students)
            .values("student_id")
            .annotate(c=Count("id"))
        )
        badge_count_map = {r["student_id"]: r["c"] for r in badge_counts}

        # Achievements count per student
        achievement_counts = (
            AchievementAcquired.objects.filter(student__in=students)
            .values("student_id")
            .annotate(c=Count("id"))
        )
        achievement_count_map = {r["student_id"]: r["c"] for r in achievement_counts}

        # Latest points balance per student (preferred), fallback to sum(points)
        latest_pt = (
            PointTransaction.objects.filter(student__in=students)
            .order_by("student_id", "-created_at")
            .values("student_id", "balance_after", "created_at")
        )
        points_balance_map = {}
        seen = set()
        for row in latest_pt:
            sid = row["student_id"]
            if sid in seen:
                continue
            seen.add(sid)
            points_balance_map[sid] = row["balance_after"]

        # Fallback sums for students with no balance entries
        missing_points_students = [s.id for s in students if s.id not in points_balance_map]
        if missing_points_students:
            sums = (
                PointTransaction.objects.filter(student_id__in=missing_points_students)
                .values("student_id")
                .annotate(total=Sum("points"))
            )
            for r in sums:
                points_balance_map[r["student_id"]] = r["total"] or 0

        # Last activity per student from ActivityEvent
        last_activity_rows = (
            ActivityEvent.objects.filter(student__in=students, organization=org)
            .order_by("student_id", "-occurred_at")
            .values("student_id", "occurred_at", "event_type")
        )
        last_activity_map = {}
        seen = set()
        for r in last_activity_rows:
            sid = r["student_id"]
            if sid in seen:
                continue
            seen.add(sid)
            last_activity_map[sid] = {
                "occurred_at": r["occurred_at"],
                "event_type": r["event_type"],
            }

        # Streak map (ForeignKey, order by most recent activity)
        streak_rows = (
            Streak.objects.filter(student__in=students)
            .order_by("student_id", "-last_activity")
            .values("student_id", "current_days", "longest_days", "last_activity")
        )
        streak_map = {}
        for r in streak_rows:
            if r["student_id"] not in streak_map:
                streak_map[r["student_id"]] = r

        # Upcoming tests (global list for events + per-child next test)
        upcoming_tests_qs = (
            Test.objects.filter(
                course__enrollments__student__in=students,
                visibility=Test.Visibility.PUBLISHED,
                start_at__gt=timezone.now(),
            )
            .select_related("course")
            .distinct()
            .order_by("start_at")
        )
        # --- Build children payload ---
        children_data = []
        total_badges = 0
        total_achievements = 0
        total_points = 0

        for link in children_links:
            student = link.student
            user = student.user

            courses_enrolled = active_map.get(student.id, 0)
            courses_completed = completed_map.get(student.id, 0)

            badge_count = badge_count_map.get(student.id, 0)
            achievement_count = achievement_count_map.get(student.id, 0)

            points_balance = points_balance_map.get(student.id, 0)

            total_badges += badge_count
            total_achievements += achievement_count
            total_points += points_balance

            streak_info = streak_map.get(student.id)
            current_streak = streak_info["current_days"] if streak_info else 0

            # Per-child next upcoming test based on enrollments
            next_test = (
                Test.objects.filter(
                    course__enrollments__student=student,
                    visibility=Test.Visibility.PUBLISHED,
                    start_at__gt=timezone.now(),
                )
                .order_by("start_at")
                .first()
            )

            upcoming_test_info = "No upcoming tests"
            if next_test:
                local_start = timezone.localtime(next_test.start_at)  # converts to current TZ (e.g. Africa/Lagos)
                upcoming_test_info = f"{next_test.title} - {local_start.strftime('%A %I:%M %p')}"

            # Last activity accurate from ActivityEvent
            la = last_activity_map.get(student.id)
            if la and la.get("occurred_at"):
                last_active = get_time_ago(la["occurred_at"])
            else:
                last_active = "No activity yet"

            child_data = {
                "id": student.id,
                "name": user.get_full_name() or user.email.split("@")[0],
                "grade": getattr(student.current_classroom, "name", "N/A"),
                "school": parent_profile.organization.name,
                "avatar": user.avatar.url if getattr(user, "avatar", None) else None,

                # Academics
                "coursesEnrolled": courses_enrolled,
                "coursesCompleted": courses_completed,
                "averageScore": round(float(avg_score_map.get(student.id, 0)), 1),

                # Gamification (accurate)
                "pointsBalance": int(points_balance),
                "badgesEarned": int(badge_count),
                "achievementsUnlocked": int(achievement_count),
                "currentStreak": int(current_streak),

                # Activity / schedule
                "lastActive": last_active,
                "upcomingTest": upcoming_test_info,

                # Backward compatibility with your existing frontend field:
                # "totalRewards" now means badges + achievements (not “weekly hours” related).
                "totalRewards": int(badge_count + achievement_count),
            }
            children_data.append(child_data)

        # --- Family stats (no study-hours) ---
        family_stats = [
            {
                "title": "Total Children",
                "value": str(len(children_data)),
                "change": "All linked",
                "icon": "Baby",
                "color": "text-purple-600",
                "bgColor": "bg-purple-100",
            },
            {
                "title": "Total Badges Earned",
                "value": str(total_badges),
                "change": "Across all children",
                "icon": "Trophy",
                "color": "text-orange-600",
                "bgColor": "bg-orange-100",
            },
            {
                "title": "Achievements Unlocked",
                "value": str(total_achievements),
                "change": "Across all children",
                "icon": "Star",
                "color": "text-blue-600",
                "bgColor": "bg-blue-100",
            },
            {
                "title": "Points Balance",
                "value": str(total_points),
                "change": "Current total",
                "icon": "Coins",
                "color": "text-green-600",
                "bgColor": "bg-green-100",
            },
        ]

        # --- Recent activity (accurate timestamps) ---
        recent_activity = []

        # Recent test attempts
        recent_tests = (
            TestAttempt.objects.filter(
                student__in=students,
                submitted_at__isnull=False,
            )
            .select_related("student__user", "test")
            .order_by("-submitted_at")[:5]
        )
        for attempt in recent_tests:
            recent_activity.append({
                "type": "test",
                "child": attempt.student.user.get_full_name() or attempt.student.user.email.split("@")[0],
                "title": f"Took {attempt.test.title}",
                "description": f"Scored {attempt.score}%",
                "time": get_time_ago(attempt.submitted_at),
                "_ts": attempt.submitted_at,
                "icon": "Target",
                "color": "text-blue-600",
            })

        # Recent achievements unlocked
        recent_achievements = (
            AchievementAcquired.objects.filter(student__in=students)
            .select_related("student__user", "definition")
            .order_by("-acquired_at")[:5]
        )
        for acq in recent_achievements:
            recent_activity.append({
                "type": "achievement",
                "child": acq.student.user.get_full_name() or acq.student.user.email.split("@")[0],
                "title": f"Unlocked {acq.definition.title}",
                "description": acq.definition.description or "Achievement unlocked!",
                "time": get_time_ago(acq.acquired_at),
                "_ts": acq.acquired_at,
                "icon": "Star",
                "color": "text-green-600",
            })

        # Recent badge awards
        recent_badges = (
            BadgeAward.objects.filter(student__in=students)
            .select_related("student__user", "badge")
            .order_by("-awarded_at")[:5]
        )
        for award in recent_badges:
            recent_activity.append({
                "type": "badge",
                "child": award.student.user.get_full_name() or award.student.user.email.split("@")[0],
                "title": f"Earned {award.badge.name}",
                "description": award.reason or award.badge.criteria or "Badge earned!",
                "time": get_time_ago(award.awarded_at),
                "_ts": award.awarded_at,
                "icon": "Trophy",
                "color": "text-orange-600",
            })

        # Recent payments (as you already had)
        recent_payments = (
            SubscriptionPayment.objects.filter(
                invoice__organization_membership__user=request.user,
                status=SubscriptionPayment.Status.SUCCESS,
            )
            .order_by("-paid_at")[:2]
        )
        for payment in recent_payments:
            recent_activity.append({
                "type": "payment",
                "child": "All Children",
                "title": "Subscription Payment",
                "description": f"₦{payment.amount} paid successfully",
                "time": get_time_ago(payment.paid_at),
                "_ts": payment.paid_at,
                "icon": "CreditCard",
                "color": "text-purple-600",
            })

        # Sort by real datetime desc, then trim, then remove internal key
        recent_activity.sort(key=lambda x: x.get("_ts") or timezone.make_aware(timezone.datetime.min), reverse=True)
        recent_activity = recent_activity[:10]
        for item in recent_activity:
            item.pop("_ts", None)

        # --- Upcoming events (tests) ---
        upcoming_events = []
        upcoming_tests = upcoming_tests_qs[:10]

        for test in upcoming_tests:
            # Which of the parent's children are enrolled in the test.course?
            for student in students:
                if Enrollment.objects.filter(student=student, course=test.course).exists():
                    importance = "high" if test.start_at <= timezone.now() + timedelta(days=2) else "medium"
                    local_start = timezone.localtime(test.start_at)  # converts to current TZ (e.g. Africa/Lagos)
                    upcoming_events.append({
                        "child": student.user.get_full_name() or student.user.email.split("@")[0],
                        "event": test.title,
                        "date": local_start.strftime("%A, %I:%M %p"),
                        "type": "Test",
                        "importance": importance,
                    })
    except Exception as e:
        print(e)

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
def children_list_view(request):
    """
    Returns a list of all children for the authenticated parent,
    but only children who have enrollments with status ACTIVE or COMPLETED.
    """
    try:
        parent_profile = ParentProfile.objects.get(user=request.user)
    except ParentProfile.DoesNotExist:
        return Response(
            {"detail": "Parent profile not found."},
            status=status.HTTP_404_NOT_FOUND
        )

    allowed_statuses = [Enrollment.Status.ACTIVE, Enrollment.Status.COMPLETED]

    children_links = (
        ParentChildLink.objects
        .filter(parent=parent_profile)
        .filter(
            student__enrollments__status__in=allowed_statuses
        )
        .select_related('student__user', 'student__current_classroom', 'student__organization')
        .distinct()
    )

    children_data = []
    for link in children_links:
        student = link.student
        children_data.append({
            "id": student.id,
            "name": student.user.get_full_name() or student.user.first_name,
            "grade": student.current_classroom.name if student.current_classroom else "N/A",
            "school": student.organization.name,
            "avatar": student.user.avatar.url if getattr(student.user, "avatar", None) else None,
        })

    return Response({
        "children": children_data,
        "totalChildren": len(children_data)
    })




@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def get_children_progress(request):
    try:
        TIME_PERIODS = {
            "week": {"days": 7, "stats_key": "weeklyStats"},
            "month": {"days": 30, "stats_key": "monthlyStats"},
            "quarter": {"days": 90, "stats_key": "quarterlyStats"},
            "semester": {"days": 180, "stats_key": "semesterStats"},
            "year": {"days": 365, "stats_key": "yearlyStats"},
        }

        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return Response(
                {"detail": "Invalid or missing session token."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        try:
            parent_profile = ParentProfile.objects.select_related("organization").get(user=user)
        except ParentProfile.DoesNotExist:
            return Response({"detail": "Parent profile not found."}, status=status.HTTP_404_NOT_FOUND)

        # ----------------------------
        # Params
        # ----------------------------
        child_id_str = (request.GET.get("child_id") or "all").strip()
        time_period = (request.GET.get("time_period") or "week").strip().lower()

        if time_period not in TIME_PERIODS:
            return Response(
                {"detail": f"Invalid time_period '{time_period}'. Allowed: {list(TIME_PERIODS.keys())}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        child_id = None
        if child_id_str != "all":
            try:
                child_id = int(child_id_str)
            except ValueError:
                return Response({"detail": "Invalid child_id format."}, status=status.HTTP_400_BAD_REQUEST)

        # ----------------------------
        # Enrollment restriction
        # ----------------------------
        allowed_statuses = [Enrollment.Status.ACTIVE, Enrollment.Status.COMPLETED]

        # ----------------------------
        # Filter children (only those with eligible enrollments)
        # ----------------------------
        child_links_qs = (
            ParentChildLink.objects.select_related(
                "student",
                "student__user",
                "student__current_classroom",
                "student__organization",
            )
            .filter(parent=parent_profile, student__user__is_active=True)
            .distinct()
        )

        if child_id is not None:
            child_links_qs = child_links_qs.filter(student__id=child_id)

        # ----------------------------
        # Time window
        # ----------------------------
        days = TIME_PERIODS[time_period]["days"]
        stats_key = TIME_PERIODS[time_period]["stats_key"]

        end = timezone.now()
        start = end - timedelta(days=days)

        children_data = []

        for link in child_links_qs:
            student = link.student
            student_user = student.user

            # Age
            age = None
            if student.dob:
                today = date.today()
                age = today.year - student.dob.year - (
                    (today.month, today.day) < (student.dob.month, student.dob.day)
                )

            # -------------------------------------
            # Attempts (ONLY courses where enrollment is ACTIVE/COMPLETED)
            # -------------------------------------
            attempts = (
                TestAttempt.objects.filter(
                    student=student,
                    submitted_at__range=(start, end),
                    status__in=["submitted", "graded"],
                    test__course__enrollments__student=student,
                    test__course__enrollments__status__in=allowed_statuses,
                )
                .select_related("test", "test__course")
                .annotate(
                    computed_total_marks=Coalesce(
                        Sum("test__questions__points"),
                        Value(Decimal("0.00")),
                        output_field=DecimalField(max_digits=10, decimal_places=2),
                    )
                )
                .distinct()
            )

            tests_completed = attempts.count()

            # -------------------------------------
            # Average score (EXCLUDE private courses)
            # -------------------------------------
            percentages = []
            for att in attempts:
                course = getattr(att.test, "course", None)
                if course and course.course_type == "private":
                    continue  # 🚫 do not include private in average

                total = att.computed_total_marks or Decimal("0")
                if total and total > 0:
                    percentages.append((att.score / total) * Decimal("100"))

            average_score = sum(percentages) / len(percentages) if percentages else Decimal("0")

            # Completed courses (keep as-is, or also restrict; this is your original meaning)
            courses_completed = Enrollment.objects.filter(
                student=student,
                status=Enrollment.Status.COMPLETED
            ).count()

            try:
                streak = Streak.objects.get(student=student).current_days
            except Streak.DoesNotExist:
                streak = 0

            stats = {
                "testsCompleted": tests_completed,
                "averageScore": int(round(average_score)),
                "streak": streak,
                "coursesCompleted": courses_completed,
            }

            # -------------------------------------
            # Per-test performance (latest attempt per test)
            # + tag private attempts
            # -------------------------------------
            tests_data = []

            latest_attempts = (
                attempts.values("test_id")
                .annotate(latest_submitted=Max("submitted_at"))
                .order_by()
            )

            for la in latest_attempts:
                attempt = attempts.filter(
                    test_id=la["test_id"],
                    submitted_at=la["latest_submitted"],
                ).first()

                if not attempt:
                    continue

                test = attempt.test
                course = getattr(test, "course", None)
                is_private = bool(course and course.course_type == "private")

                total = getattr(attempt, "computed_total_marks", None)
                if total is None:
                    total = test.questions.aggregate(t=Coalesce(Sum("points"), 0))["t"]

                percentage = (attempt.score / total) * Decimal("100") if total and total > 0 else Decimal("0")

                if percentage >= Decimal("70"):
                    grade = "A"
                elif percentage >= Decimal("60"):
                    grade = "B"
                elif percentage >= Decimal("50"):
                    grade = "C"
                elif percentage >= Decimal("45"):
                    grade = "D"
                else:
                    grade = "F"

                # Previous attempt (also restricted to eligible enrollments)
                prev_attempt = (
                    TestAttempt.objects.filter(
                        student=student,
                        test=test,
                        submitted_at__lt=attempt.submitted_at,
                        status__in=["submitted", "graded"],
                        test__course__enrollments__student=student,
                        test__course__enrollments__status__in=allowed_statuses,
                    )
                    .order_by("-submitted_at")
                    .first()
                )

                trend = "stable"
                if prev_attempt:
                    prev_total = prev_attempt.test.questions.aggregate(
                        t=Coalesce(
                            Sum("points"),
                            Value(Decimal("0.00")),
                            output_field=DecimalField(max_digits=10, decimal_places=2),
                        )
                    )["t"]

                    prev_percentage = (
                        (prev_attempt.score / prev_total) * Decimal("100")
                        if prev_total and prev_total > 0
                        else Decimal("0")
                    )

                    if percentage > prev_percentage + Decimal("5"):
                        trend = "up"
                    elif percentage < prev_percentage - Decimal("5"):
                        trend = "down"

                test_title = test.title
                if course and getattr(course, "name", None):
                    test_title = f"{course.name} - {test.title}"

                tests_data.append(
                    {
                        "name": test_title,
                        "grade": grade,
                        "lastScore": int(round(percentage)),
                        "trend": trend,

                        # ✅ new fields for frontend tag
                        "isPrivate": is_private,
                        "tag": "Private" if is_private else None,
                    }
                )

            # -------------------------------------
            # Subscription/status
            # -------------------------------------
            is_active = student.organization.is_active
            subscription_status = "Basic"
            if (
                getattr(parent_profile, "organization_subscription", None)
                and parent_profile.organization_subscription.status == "active"
            ):
                subscription_status = "Premium"
            parent_profile_id = parent_profile.id
            last_login = "Not Known"
            if student.user.last_login:
                last_login = student.user.last_login.strftime("%Y-%m-%d")
            child_data = {
                "id": student.id,
                "parent_profile_id":parent_profile_id,
                "name": student_user.get_full_name() or student_user.email,
                "age": age,
                "grade": student.current_classroom.name if student.current_classroom else "Not Assigned",
                "school": student.organization.name,
                "avatar": student_user.avatar.url if getattr(student_user, "avatar", None) else None,
                "email": student_user.email,
                "status": "Active" if is_active else "Suspended",
                "subscription": subscription_status,
                "lastActive": last_login,
                "joinDate": student.created_at.strftime("%Y-%m-%d"),
                "totalCourses": Enrollment.objects.filter(student=student).count(),
                "completedCourses": Enrollment.objects.filter(
                    student=student, status=Enrollment.Status.COMPLETED
                ).count(),
                "relationship": link.relationship,
                "admissionNo": student.admission_no,
                "subjects": tests_data,

                # put stats into chosen key
                stats_key: stats,
                "stats": stats,
            }

            children_data.append(child_data)

        return Response({"children": children_data}, status=status.HTTP_200_OK)

    except Exception as e:
        print(e)
        return Response({"children": []}, status=status.HTTP_400_BAD_REQUEST)


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
            student__id=child_id
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



class ResetPasswordView(APIKeySessionViewSet):
    @transaction.atomic
    def post(self, request, *args, **kwargs):
        serializer = ResetPasswordSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)

        target_user = serializer.validated_data["target_user"]
        new_password = serializer.validated_data["new_password"]
        new_email = serializer.validated_data.get("new_email", None)

        # Set new password now
        target_user.set_password(new_password)

        email_changed = False
        verification_required = bool(getattr(settings, "EMAIL_CHANGE_VERIFY", True)) and bool(new_email)

        # If new_email provided
        if new_email:
            if verification_required:
                # Create EmailChangeRequest and send OTP
                minutes = getattr(settings, "EMAIL_CHANGE_CODE_LIFETIME_MINUTES", 15)
                req = EmailChangeRequest.create_request(target_user, new_email, minutes_valid=minutes)

                # Build a simple email — customize as you like
                subject = "Confirm your new email address"
                # For link flow, you'd build a URL containing the code + user id; here we'll use code OTP
                message = (
                    f"Hello {getattr(target_user, 'email', '')},\n\n"
                    f"You (or someone with access to your account) requested to change the email to {new_email}.\n\n"
                    f"Please use this verification code to confirm your new email (valid for {minutes} minutes):\n\n"
                    f"{req.code}\n\n"
                    "If you did not request this, contact support or ignore this email.\n"
                )
                from_email = getattr(settings, "DEFAULT_FROM_EMAIL", None)
                # NOTE: If you prefer confirmation link, create a URL with code & user id and send that instead.
                try:
                    send_mail(
                        subject,
                        message,
                        from_email,
                        [new_email],
                        fail_silently=False,
                    )
                    otp_sent = True
                except Exception:
                    # If email sending fails, we still don't want to leak too much; raise or return an error
                    # You may log the exception in production.
                    return Response({"detail": "Failed to send verification email."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

            else:
                # verification not required -> update right away
                if target_user.email != new_email:
                    target_user.email = new_email
                    email_changed = True

        target_user.is_generated = False
        target_user.save()

        resp = {
            "detail": "Password reset successfully.",
            "user_id": target_user.id,
            "user_email": getattr(target_user, "email", None),
            "email_changed": email_changed,
        }

        if new_email and verification_required:
            resp.update({
                "email_verification_required": True,
                "email_sent": True,
                # Optionally don't reveal the code or too many details
            })

        return Response(resp, status=status.HTTP_200_OK)


# Confirm view to apply pending email change
class ConfirmEmailChangeView(APIKeySessionViewSet):
    """
    POST /api/accounts/confirm-email-change/
    Body:
    {
      "code": "123456"
      # for admin flows optionally include "user_id": 42 (if confirming on behalf)
    }
    """
    @transaction.atomic
    def post(self, request, *args, **kwargs):
        code = request.data.get("code")
        user = request.user

        if not code:
            return Response({"code": "This field is required."}, status=status.HTTP_400_BAD_REQUEST)

        # Find pending request: code is unique only per user/new_email combination
        try:
            req = EmailChangeRequest.objects.select_for_update().get(user=user, code=code, used=False)
        except EmailChangeRequest.DoesNotExist:
            return Response({"code": "Invalid code."}, status=status.HTTP_400_BAD_REQUEST)

        if not req.is_valid():
            return Response({"code": "Code expired or already used."}, status=status.HTTP_400_BAD_REQUEST)

        # Apply the change (but ensure uniqueness again, race-safe)
        normalized = User.objects.normalize_email(req.new_email)
        conflict = User.objects.filter(email__iexact=normalized).exclude(pk=user.pk).exists()
        if conflict:
            return Response({"detail": "That email is already used by another account."}, status=status.HTTP_400_BAD_REQUEST)

        user.email = normalized
        user.save()

        req.used = True
        req.save()

        # Optionally expire other pending requests for the same email or by user
        EmailChangeRequest.objects.filter(user=user, used=False).exclude(pk=req.pk).update(used=True)

        return Response({"detail": "Email updated successfully.", "user_email": user.email}, status=status.HTTP_200_OK)

@api_view(["POST"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def update_profile(request):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return Response({"detail": "Invalid or missing session token."},
                        status=status.HTTP_401_UNAUTHORIZED)

    data = request.data or {}
    account_type = (data.get("account_type") or "").strip().lower() or None

    # auto detect role if not provided
    def detect_account_type(u):
        if hasattr(u, "teacher_profile"):
            return "teacher"
        if hasattr(u, "student_profile"):
            return "student"
        if hasattr(u, "parent_profile"):
            return "parent"
        return None

    if not account_type:
        account_type = detect_account_type(user)

    if account_type not in ("teacher", "student", "parent"):
        return Response({"detail": "account_type must be one of: teacher, student, parent"},
                        status=status.HTTP_400_BAD_REQUEST)

    try:
        with transaction.atomic():

            # COMMON USER FIELD UPDATE FUNCTION
            def update_user_fields():
                if "email" in data:
                    email = data.get("email", "").strip()
                    if not email:
                        return Response({"detail": "email cannot be empty."}, status=status.HTTP_400_BAD_REQUEST)

                    if User.objects.filter(email=email).exclude(pk=user.pk).exists():
                        return Response({"detail": "Email already in use."}, status=status.HTTP_400_BAD_REQUEST)

                    user.email = email

                if "first_name" in data:
                    user.first_name = data.get("first_name", "").strip()

                if "last_name" in data:
                    user.last_name = data.get("last_name", "").strip()

                if "phone" in data:
                    user.phone = data.get("phone", "").strip()

                user.save()
                return None

            # TEACHER
            if account_type == "teacher":
                try:
                    teacher = user.teacher_profile
                except ObjectDoesNotExist:
                    return Response({"detail": "Teacher profile not found."}, status=status.HTTP_404_NOT_FOUND)

                err = update_user_fields()
                if err:
                    return err

                if "bio" in data:
                    teacher.bio = data.get("bio", "").strip()

                if "experience" in data:
                    try:
                        exp = int(data.get("experience"))
                        if exp < 0:
                            raise ValueError()
                    except Exception:
                        return Response({"detail": "experience must be a non-negative integer."},
                                        status=status.HTTP_400_BAD_REQUEST)
                    teacher.experience = exp

                teacher.save()

                return Response({
                    "detail": "Teacher profile updated.",
                    "user": {
                        "id": user.id,
                        "email": user.email,
                        "first_name": user.first_name,
                        "last_name": user.last_name,
                        "phone": user.phone
                    },
                    "teacher_profile": {
                        "bio": teacher.bio,
                        "experience": teacher.experience
                    }
                })

            # STUDENT
            if account_type == "student":
                try:
                    student = user.student_profile
                except ObjectDoesNotExist:
                    return Response({"detail": "Student profile not found."}, status=status.HTTP_404_NOT_FOUND)

                err = update_user_fields()
                if err:
                    return err

                return Response({
                    "detail": "Student profile updated.",
                    "user": {
                        "id": user.id,
                        "email": user.email,
                        "first_name": user.first_name,
                        "last_name": user.last_name,
                        "phone": user.phone
                    }
                })

            # PARENT (UPDATED PART)
            if account_type == "parent":
                try:
                    parent = user.parent_profile
                except ObjectDoesNotExist:
                    return Response({"detail": "Parent profile not found."}, status=status.HTTP_404_NOT_FOUND)

                err = update_user_fields()
                if err:
                    return err

                if "address" in data:
                    parent.address = data.get("address", "").strip()

                parent.save()

                return Response({
                    "detail": "Parent profile updated.",
                    "user": {
                        "id": user.id,
                        "email": user.email,
                        "first_name": user.first_name,
                        "last_name": user.last_name,
                        "phone": user.phone
                    },
                    "parent_profile": {
                        "address": parent.address
                    }
                })

    except IntegrityError:
        return Response({"detail": "Database error updating profile."},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    except Exception as e:
        return Response({"detail": str(e)},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR)



@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def fetch_profile(request):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return Response(
            {"detail": "Invalid or missing session token."},
            status=status.HTTP_401_UNAUTHORIZED
        )

    # GET uses query params instead of request.data
    account_type = (request.query_params.get("account_type") or "").strip().lower() or None

    # auto detect role if not provided
    def detect_account_type(u):
        if hasattr(u, "teacher_profile"):
            return "teacher"
        if hasattr(u, "student_profile"):
            return "student"
        if hasattr(u, "parent_profile"):
            return "parent"
        return None

    if not account_type:
        account_type = detect_account_type(user)

    if account_type not in ("teacher", "student", "parent"):
        return Response(
            {"detail": "account_type must be one of: teacher, student, parent"},
            status=status.HTTP_400_BAD_REQUEST
        )

    # COMMON USER DATA
    user_data = {
        "id": user.id,
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "phone": user.phone
    }

    # TEACHER
    if account_type == "teacher":
        try:
            teacher = user.teacher_profile
        except ObjectDoesNotExist:
            return Response({"detail": "Teacher profile not found."}, status=status.HTTP_404_NOT_FOUND)

        return Response({
            "account_type": "teacher",
            "user": user_data,
            "teacher_profile": {
                "bio": teacher.bio,
                "experience": teacher.experience
            }
        })

    # STUDENT
    if account_type == "student":
        try:
            student = user.student_profile
        except ObjectDoesNotExist:
            return Response({"detail": "Student profile not found."}, status=status.HTTP_404_NOT_FOUND)

        return Response({
            "account_type": "student",
            "user": user_data
        },status=status.HTTP_200_OK)

    # PARENT
    if account_type == "parent":
        try:
            parent = user.parent_profile
        except ObjectDoesNotExist:
            return Response({"detail": "Parent profile not found."}, status=status.HTTP_404_NOT_FOUND)

        return Response({
            "account_type": "parent",
            "user": user_data,
            "parent_profile": {
                "address": parent.address
            },
        }, status=status.HTTP_200_OK
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