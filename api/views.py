import json
import traceback
from decimal import Decimal
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import authenticate
from django.db import transaction
from django.db.models import Q, Sum, Count, F
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework import status, viewsets
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.response import Response
from rest_framework import permissions
from rest_framework_api_key.permissions import HasAPIKey
from accounts.models import User
from .authentication import SessionTokenAuthentication
from .models import SessionToken
from .serializers import (
    OrganizationSerializer, OrganizationMembershipSerializer, AcademicSessionSerializer,
    ClassroomSerializer, SubjectSerializer, StudentProfileSerializer, TeacherProfileSerializer,
    ParentProfileSerializer, ParentChildLinkSerializer,
    CourseSerializer, EnrollmentSerializer, ModuleSerializer, LessonSerializer, MaterialSerializer,
    BookmarkSerializer, NoteSerializer,
    TestSerializer, QuestionSerializer, ChoiceSerializer, TestAttemptSerializer, AssignmentSerializer, SubmissionSerializer,
    AttendanceSessionSerializer, AttendanceRecordSerializer,
    BadgeSerializer, BadgeAwardSerializer, PointTransactionSerializer, StreakSerializer,
    LiveSessionSerializer, TutoringBookingSerializer,TutoringBookingTeacherSerializer,
    SubscriptionPlanSerializer, OrganizationSubscriptionSerializer, SubscriptionInvoiceSerializer, SubscriptionPaymentSerializer,
    NotificationSerializer,PrivateTutoringSerializer
)

from orgs.models import Organization, OrganizationMembership, AcademicSession
from academics.models import (
    Classroom, Subject, StudentProfile, TeacherProfile,
    ParentProfile, ParentChildLink,
)
from learning.models import Course, Enrollment, Module, Lesson, Material, Bookmark, Note
from assessments.models import Test, Question, Choice, TestAttempt, Assignment, Submission
from attendance.models import AttendanceSession, AttendanceRecord
from gamification.models import Badge, BadgeAward, PointTransaction, Streak
from live.models import PrivateTutoring, AvailableDay, TutoringBooking, LiveSession
from billing.models import (
    SubscriptionPlan, OrganizationSubscription, SubscriptionInvoice, SubscriptionPayment,
)
from notifications.models import Notification
from django.shortcuts import get_object_or_404
from core.utils import (_get_teacher_for_user, _get_student_for_user,get_object_or_404_ajax,
    _resolve_org,_to_int,_is_org_admin_or_teacher, IsOwnerOrOrgStaff, _lesson_belongs_to_org)
from django.urls import reverse
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.db.models import Subquery
from rest_framework.permissions import IsAuthenticated


# ...your other imports
@api_view(["POST"])
@permission_classes([HasAPIKey])
@authentication_classes([])
def login_view(request):
    email = request.data.get("email") or request.data.get("username")  # accept either key during rollout
    password = request.data.get("password")
    hours_valid = int(request.data.get("hours_valid") or 24)

    if not email or not password:
        return Response({"detail": "email and password are required."}, status=status.HTTP_400_BAD_REQUEST)

    user = authenticate(request, email=email, password=password)  # <— key change
    if not user:
        return Response({"detail": "Invalid credentials."}, status=status.HTTP_401_UNAUTHORIZED)

    st = SessionToken.create_for_user(user, hours_valid=hours_valid, ip=request.META.get("REMOTE_ADDR"))
    return Response({"sessionToken": st.key, "expiresAt": st.expires_at.isoformat(), "userId": user.id})




def _issue_password_reset_token(user, hours_valid=1, ip=None):
    """
    Create a short-lived SessionToken specifically for password resets.
    """
    return SessionToken.create_for_user(
        user,
        hours_valid=hours_valid,
        ip=ip,
        purpose="password_reset",  # tagged for later validation
    )


def _send_password_reset_email(user, reset_token, request, reset_path=None):
    """
    Minimal email sender. Replace with your templating / email backend as needed.
    """
    # Build a link your frontend can handle. Adjust domain/origin and path to taste.
    # If you have a dedicated frontend URL, use it here.
    origin = getattr(settings, "FRONTEND_ORIGIN", "").rstrip("/") or request.build_absolute_uri("/").rstrip("/")
    if reset_path is None:
        reset_path = "reset-password"  # e.g., your frontend route
    reset_url = f"{origin}/{reset_path}?token={reset_token.key}"

    subject = "Reset your password"
    # If you have a template, use it; otherwise send a simple text message
    message = (
        f"Hi,\n\nWe received a request to reset your password.\n\n"
        f"Use this link to set a new one (valid until {reset_token.expires_at.isoformat()}):\n{reset_url}\n\n"
        f"If you didn’t request this, you can ignore this email."
    )

    # Fail silently to avoid leaking existence; log as needed.
    send_mail(
        subject,
        message,
        getattr(settings, "DEFAULT_FROM_EMAIL", None),
        [user.email],
        fail_silently=True,
    )
    print("mail_sent....")


def _find_active_token(key, purpose="password_reset"):
    """
    Look up an active, unexpired SessionToken by key and purpose.
    """
    now = timezone.now()
    try:
        st = SessionToken.objects.select_related("user").get(
            key=key,
            is_active=True,
            expires_at__gt=now,
        )
    except SessionToken.DoesNotExist:
        return None

    # Enforce purpose tagging if present
    if st.meta.get("purpose") != purpose:
        return None
    return st


@api_view(["POST"])
@permission_classes([HasAPIKey])
@authentication_classes([])  # no auth; guarded by API key and generic response
def password_reset_request_view(request):
    """
    Start password reset by email.
    Body:
      - email: required
      - hours_valid: optional (default 1)
    Always returns 200 for privacy (no user enumeration).
    """
    email = (request.data.get("email") or "").strip().lower()
    reset_path = request.data.get("reset_path", None)
    hours_valid = int(request.data.get("hours_valid") or 1)
    if not email:
        return Response({"detail": "email is required."}, status=status.HTTP_400_BAD_REQUEST)

    # Try to find user by email (case-insensitive)
    try:
        user = User.objects.get(email__iexact=email, is_active=True)
    except User.DoesNotExist:
        # Return generic success to avoid user enumeration
        return Response({"detail": "Account not found."}, status=status.HTTP_404_NOT_FOUND)

    # Issue short-lived reset token and email it
    st = _issue_password_reset_token(user, hours_valid=hours_valid, ip=request.META.get("REMOTE_ADDR"))
    _send_password_reset_email(user, st, request, reset_path)

    return Response({"detail": "If an account exists, a reset link has been sent."})


@api_view(["POST"])
@permission_classes([HasAPIKey])
@authentication_classes([])  # guarded by API key; token is in body
def password_reset_confirm_view(request):
    """
    Confirm password reset using the reset token.
    Body:
      - resetToken (alias: token)
      - new_password
      - re_new_password
      - issue_session_hours (optional) -> if provided, returns a fresh login sessionToken
    """
    token = request.data.get("resetToken") or request.data.get("token") or ""
    new_password = request.data.get("new_password") or ""
    re_new_password = request.data.get("re_new_password") or ""
    issue_hours = request.data.get("issue_session_hours")

    if not token:
        return Response({"detail": "resetToken is required."}, status=status.HTTP_400_BAD_REQUEST)
    if not new_password or not re_new_password:
        return Response({"detail": "new_password and re_new_password are required."}, status=status.HTTP_400_BAD_REQUEST)
    if new_password != re_new_password:
        return Response({"detail": "Passwords do not match."}, status=status.HTTP_400_BAD_REQUEST)

    st = _find_active_token(token, purpose="password_reset")
    if not st:
        return Response({"detail": "Invalid or expired token."}, status=status.HTTP_400_BAD_REQUEST)

    user = st.user
    # Optionally enforce password validators here if you use them.

    user.set_password(new_password)
    user.save(update_fields=["password"])

    # Revoke the reset token
    st.revoke()

    response_data = {"detail": "Password has been reset successfully."}

    # Optionally: revoke all existing regular session tokens for security
    # SessionToken.objects.filter(user=user, is_active=True).exclude(pk=st.pk).update(is_active=False)

    # Optionally issue a fresh login session token so the user is signed in immediately
    if issue_hours:
        try:
            hours = int(issue_hours)
            new_st = SessionToken.create_for_user(
                user,
                hours_valid=hours,
                ip=request.META.get("REMOTE_ADDR"),
                purpose="session",  # mark as a normal session
            )
            response_data.update({
                "sessionToken": new_st.key,
                "expiresAt": new_st.expires_at.isoformat(),
                "userId": user.id,
            })
        except Exception:
            # ignore issuance errors; password reset already succeeded
            pass

    return Response(response_data, status=status.HTTP_200_OK)



@api_view(["POST"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def logout_view(request):
    token = request.META.get("HTTP_X_SESSION_TOKEN")
    if not token:
        auth = request.META.get("HTTP_AUTHORIZATION", "")
        if auth.startswith("Session "):
            token = auth[len("Session "):].strip()

    if not token:
        return Response({"detail": "No session token found in headers."}, status=status.HTTP_400_BAD_REQUEST)

    try:
        st = SessionToken.objects.get(key=token, is_active=True)
        st.revoke()
    except SessionToken.DoesNotExist:
        pass

    return Response({"detail": "Logged out."}, status=status.HTTP_200_OK)


class APIKeySessionViewSet(viewsets.ModelViewSet):
    permission_classes = [HasAPIKey]
    authentication_classes = [SessionTokenAuthentication]


class OrganizationViewSet(APIKeySessionViewSet):
    queryset = Organization.objects.all()
    serializer_class = OrganizationSerializer

class OrganizationMembershipViewSet(APIKeySessionViewSet):
    queryset = OrganizationMembership.objects.all()
    serializer_class = OrganizationMembershipSerializer

class AcademicSessionViewSet(APIKeySessionViewSet):
    queryset = AcademicSession.objects.all()
    serializer_class = AcademicSessionSerializer

class ClassroomViewSet(APIKeySessionViewSet):
    queryset = Classroom.objects.all()
    serializer_class = ClassroomSerializer


    def get_queryset(self):
        # Call your existing org resolver
        org, error_response = _resolve_org(self.request)
        if error_response is not None:
            # Store it to handle later in the list() or retrieve() method
            # because get_queryset() must always return a queryset
            self._org_error = error_response
            return Classroom.objects.none()

        self._org_error = None
        return Classroom.objects.filter(organization=org)

    def list(self, request, *args, **kwargs):
        # Handle the case where _resolve_org returned an error Response
        if hasattr(self, "_org_error") and self._org_error is not None:
            return self._org_error
        return super().list(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        if hasattr(self, "_org_error") and self._org_error is not None:
            return self._org_error
        return super().retrieve(request, *args, **kwargs)


class SubjectViewSet(APIKeySessionViewSet):
    queryset = Subject.objects.all()
    serializer_class = SubjectSerializer

class StudentProfileViewSet(APIKeySessionViewSet):
    queryset = StudentProfile.objects.all()
    serializer_class = StudentProfileSerializer

class TeacherProfileViewSet(APIKeySessionViewSet):
    queryset = TeacherProfile.objects.all()
    serializer_class = TeacherProfileSerializer

class ParentProfileViewSet(APIKeySessionViewSet):
    queryset = ParentProfile.objects.all()
    serializer_class = ParentProfileSerializer

class ParentChildLinkViewSet(APIKeySessionViewSet):
    queryset = ParentChildLink.objects.all()
    serializer_class = ParentChildLinkSerializer


class CourseViewSet(APIKeySessionViewSet):
    queryset = Course.objects.all()
    serializer_class = CourseSerializer

class EnrollmentViewSet(APIKeySessionViewSet):
    queryset = Enrollment.objects.all()
    serializer_class = EnrollmentSerializer

class ModuleViewSet(APIKeySessionViewSet):
    queryset = Module.objects.all()
    serializer_class = ModuleSerializer


class LessonViewSet(APIKeySessionViewSet):
    serializer_class = LessonSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        try:
            request = self.request
            org,msg = _resolve_org(request)
            if not org:
                return Lesson.objects.none()

            # Base: only lessons inside this org (via course on module)
            qs = (
                Lesson.objects.filter(
                    active=True,
                    module__active=True,
                    module__course__is_active=True,
                    # ⬇️ use *_id to avoid instance/class mismatch
                    module__course__organization_id=org.id,
                )
                .select_related(
                    "module",
                    "module__course",
                    "module__course__teacher",
                    "module__course__teacher__user",
                )
                .order_by("module_id", "order")
            )

            user = request.user
            # If the requester is a student: restrict to courses they’re actively enrolled in
            student = getattr(user, "student_profile", None)
            if isinstance(student, StudentProfile):
                if student.organization_id != org.id:
                    return Lesson.objects.none()

                enrolled_course_ids = Enrollment.objects.filter(
                    student=student,
                    status=Enrollment.Status.ACTIVE,
                ).values("course_id")
                qs = qs.filter(module__course_id__in=Subquery(enrolled_course_ids))

            # If the requester is a teacher: show only their courses in this org (when not staff)
            teacher = getattr(user, "teacher_profile", None)
            if isinstance(teacher, TeacherProfile) and not user.is_staff and not user.is_superuser:
                if teacher.organization_id != org.id:
                    return Lesson.objects.none()
                qs = qs.filter(module__course__teacher_id=teacher.id)

            # Optional query params:
            module_id = request.query_params.get("module")
            if module_id:
                qs = qs.filter(module_id=module_id)

            course_id = request.query_params.get("course")
            if course_id:
                qs = qs.filter(module__course_id=course_id)

            return qs

        except Exception as e:
            # Prefer logging in production:
            # logger.exception("Error in LessonViewSet.get_queryset")
            print(f"Error in LessonViewSet.get_queryset: {str(e)}")
            return Lesson.objects.none()


class MaterialViewSet(APIKeySessionViewSet):
    queryset = Material.objects.all()
    serializer_class = MaterialSerializer



class BookmarkViewSet(APIKeySessionViewSet):
    queryset = Bookmark.objects.all()
    serializer_class = BookmarkSerializer
    permission_classes = [permissions.IsAuthenticated, IsOwnerOrOrgStaff]

    def get_queryset(self):
        """
        Admins/teachers: all bookmarks in org (optionally filtered by lesson).
        Students: only their bookmarks (still ensuring lesson is in org).
        """
        request = self.request
        org, org_error_response = _resolve_org(request)
        if org_error_response:
            # If your APIKeySessionViewSet already handles responses,
            # you could raise an APIException; returning 0 rows also works,
            # but raising is clearer:
            raise PermissionDenied(detail=org_error_response.data.get("detail", "Organization access denied."))

        qs = (
            Bookmark.objects
            .select_related("student__organization", "lesson__module__course__organization")
        )

        lesson_id = request.query_params.get("lesson")
        if _is_org_admin_or_teacher(request, org):
            qs = qs.filter(lesson__module__course__organization=org)
            if lesson_id:
                qs = qs.filter(lesson_id=lesson_id)
            return qs

        # Student scope
        sp = _get_student_for_user(request.user)
        if not sp or sp.organization_id != org.id:
            # No student profile in this org => no access
            return qs.none()

        qs = qs.filter(student=sp, lesson__module__course__organization=org)
        if lesson_id:
            qs = qs.filter(lesson_id=lesson_id)
        return qs

    def perform_create(self, serializer):
        request = self.request
        org, _ = _resolve_org(request)

        sp = _get_student_for_user(request.user)
        if not sp or sp.organization_id != org.id:
            raise PermissionDenied("You are not a student in this organization.")

        lesson = serializer.validated_data.get("lesson")
        if not lesson or not _lesson_belongs_to_org(lesson, org):
            raise ValidationError({"lesson": "Lesson does not belong to this organization."})

        serializer.save(student=sp)

    def perform_update(self, serializer):
        request = self.request
        org, _ = _resolve_org(request)

        instance: Bookmark = self.get_object()
        # Permission class will block non-owners already, but we still validate lesson/org on payload changes
        lesson = serializer.validated_data.get("lesson", instance.lesson)
        if not _lesson_belongs_to_org(lesson, org):
            raise ValidationError({"lesson": "Lesson does not belong to this organization."})
        serializer.save()

    def perform_destroy(self, instance):
        # IsOwnerOrOrgStaff enforces ownership/admin; queryset already org-scoped
        super().perform_destroy(instance)


class NoteViewSet(APIKeySessionViewSet):
    queryset = Note.objects.all()
    serializer_class = NoteSerializer
    permission_classes = [permissions.IsAuthenticated, IsOwnerOrOrgStaff]

    def get_queryset(self):
        """
        Admins/teachers: all notes in org (optionally filtered by lesson).
        Students: only their notes (still ensuring lesson is in org).
        """
        request = self.request
        org, org_error_response = _resolve_org(request)
        if org_error_response:
            raise PermissionDenied(detail=org_error_response.data.get("detail", "Organization access denied."))

        qs = (
            Note.objects
            .select_related("student__organization", "lesson__module__course__organization")
        )

        lesson_id = request.query_params.get("lesson")
        if _is_org_admin_or_teacher(request, org):
            qs = qs.filter(lesson__module__course__organization=org)
            if lesson_id:
                qs = qs.filter(lesson_id=lesson_id)
            return qs

        sp = _get_student_for_user(request.user)
        if not sp or sp.organization_id != org.id:
            return qs.none()

        qs = qs.filter(student=sp, lesson__module__course__organization=org)
        if lesson_id:
            qs = qs.filter(lesson_id=lesson_id)
        return qs

    def perform_create(self, serializer):
        request = self.request
        org, _ = _resolve_org(request)

        sp = _get_student_for_user(request.user)
        if not sp or sp.organization_id != org.id:
            raise PermissionDenied("You are not a student in this organization.")

        lesson = serializer.validated_data.get("lesson")
        if not lesson or not _lesson_belongs_to_org(lesson, org):
            raise ValidationError({"lesson": "Lesson does not belong to this organization."})

        serializer.save(student=sp)

    def perform_update(self, serializer):
        request = self.request
        org, _ = _resolve_org(request)

        instance: Note = self.get_object()
        lesson = serializer.validated_data.get("lesson", instance.lesson)
        if not _lesson_belongs_to_org(lesson, org):
            raise ValidationError({"lesson": "Lesson does not belong to this organization."})
        serializer.save()

    def perform_destroy(self, instance):
        super().perform_destroy(instance)



class TestViewSet(APIKeySessionViewSet):
    queryset = Test.objects.all()
    serializer_class = TestSerializer

class QuestionViewSet(APIKeySessionViewSet):
    queryset = Question.objects.all()
    serializer_class = QuestionSerializer

class ChoiceViewSet(APIKeySessionViewSet):
    queryset = Choice.objects.all()
    serializer_class = ChoiceSerializer

class TestAttemptViewSet(APIKeySessionViewSet):
    queryset = TestAttempt.objects.all()
    serializer_class = TestAttemptSerializer

class AssignmentViewSet(APIKeySessionViewSet):
    queryset = Assignment.objects.all()
    serializer_class = AssignmentSerializer

class SubmissionViewSet(APIKeySessionViewSet):
    queryset = Submission.objects.all()
    serializer_class = SubmissionSerializer


class AttendanceSessionViewSet(APIKeySessionViewSet):
    queryset = AttendanceSession.objects.all()
    serializer_class = AttendanceSessionSerializer

class AttendanceRecordViewSet(APIKeySessionViewSet):
    queryset = AttendanceRecord.objects.all()
    serializer_class = AttendanceRecordSerializer


class BadgeViewSet(APIKeySessionViewSet):
    queryset = Badge.objects.all()
    serializer_class = BadgeSerializer

class BadgeAwardViewSet(APIKeySessionViewSet):
    queryset = BadgeAward.objects.all()
    serializer_class = BadgeAwardSerializer

class PointTransactionViewSet(APIKeySessionViewSet):
    queryset = PointTransaction.objects.all()
    serializer_class = PointTransactionSerializer

class StreakViewSet(APIKeySessionViewSet):
    queryset = Streak.objects.all()
    serializer_class = StreakSerializer


class LiveSessionViewSet(APIKeySessionViewSet):
    queryset = LiveSession.objects.all()
    serializer_class = LiveSessionSerializer

class TutoringBookingViewSet(APIKeySessionViewSet):
    queryset = TutoringBooking.objects.all()
    serializer_class = TutoringBookingSerializer


class SubscriptionPlanViewSet(APIKeySessionViewSet):
    queryset = SubscriptionPlan.objects.all()
    serializer_class = SubscriptionPlanSerializer

class OrganizationSubscriptionViewSet(APIKeySessionViewSet):
    queryset = OrganizationSubscription.objects.all()
    serializer_class = OrganizationSubscriptionSerializer

class SubscriptionInvoiceViewSet(APIKeySessionViewSet):
    queryset = SubscriptionInvoice.objects.all()
    serializer_class = SubscriptionInvoiceSerializer

class SubscriptionPaymentViewSet(APIKeySessionViewSet):
    queryset = SubscriptionPayment.objects.all()
    serializer_class = SubscriptionPaymentSerializer


class NotificationViewSet(APIKeySessionViewSet):
    queryset = Notification.objects.all()
    serializer_class = NotificationSerializer




def _get_parent_for_user(user):
    """Fetch the ParentProfile for the current user (or None)."""
    # Import here if your project structure differs
    from academics.models import ParentProfile  # adjust if ParentProfile is elsewhere
    try:
        return ParentProfile.objects.get(user=user)
    except ParentProfile.DoesNotExist:
        return None


def _serialize_booking(b: TutoringBooking):
    return {
        "id": b.id,
        "private_tutoring_id": b.private_tutoring_id,
        "teacher_id": b.teacher_id,
        "student_id": b.student_id,
        "duration_hours": b.duration_hours,
        "price": str(b.price),
        "status": b.status,
        "notes": b.notes or "",
        "created_at": getattr(b, "created", None) or getattr(b, "created_at", None),
        "updated_at": getattr(b, "modified", None) or getattr(b, "updated_at", None),
    }


@api_view(["POST"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def upsert_tutoring_booking(request):
    """
    Create or update a TutoringBooking on behalf of a parent for their linked child.

    Expected JSON:
    {
        // REQUIRED
        "student_id": <int>,

        // Either provide private_tutoring_id OR (teacher_id AND course_id)
        "private_tutoring_id": <int>,    // optional
        "teacher_id": <int>,             // optional
        "course_id": <int>,              // optional

        // Booking attributes
        "duration_hours": <int>,         // optional, default 2
        "notes": "<string>",             // optional
        "status": "pending|confirmed|completed|cancelled" // optional (parents usually set pending)

        // To update an existing booking (must belong to this parent/child):
        "booking_id": <int>              // optional
    }
    """
    try:
        user = request.user
        parent = _get_parent_for_user(user)
        if not parent:
            return Response({"detail": "Parent profile not found."}, status=status.HTTP_403_FORBIDDEN)

        data = request.data

        # ---- Validate child (must be linked to parent) ----
        student_id = data.get("student_id")
        if not student_id:
            return Response({"detail": "student_id is required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            student = StudentProfile.objects.select_related("user", "organization").get(pk=student_id)
        except StudentProfile.DoesNotExist:
            student = get_object_or_404_ajax(User, pk=student_id)
            if student:
                student = student.student_profile
            else:
                return Response({"detail": "Student not found."}, status=status.HTTP_404_NOT_FOUND)

        # Verify parent-child link
        is_linked = ParentChildLink.objects.filter(parent=parent, student=student).exists()
        if not is_linked:
            return Response(
                {"detail": "This student is not linked to your parent account."},
                status=status.HTTP_403_FORBIDDEN
            )

        # ---- Resolve PrivateTutoring ----
        private_tutoring_id = data.get("private_tutoring_id")
        teacher_id = data.get("teacher_id")
        course_id = data.get("course_id")

        private_tutoring = None
        if private_tutoring_id:
            try:
                private_tutoring = (
                    PrivateTutoring.objects
                    .select_related("teacher", "course", "teacher__user")
                    .get(pk=private_tutoring_id)
                )
            except PrivateTutoring.DoesNotExist:
                return Response({"detail": "PrivateTutoring not found."}, status=status.HTTP_404_NOT_FOUND)
        else:
            # fallback to resolving from (teacher, course)
            if not (teacher_id and course_id):
                return Response(
                    {"detail": "Provide either private_tutoring_id OR (teacher_id and course_id)."},
                    status=status.HTTP_400_BAD_REQUEST
                )
            try:
                teacher = TeacherProfile.objects.select_related("user").get(pk=teacher_id)
            except TeacherProfile.DoesNotExist:
                return Response({"detail": "Teacher not found."}, status=status.HTTP_404_NOT_FOUND)

            try:
                course = Course.objects.get(pk=course_id)
            except Course.DoesNotExist:
                return Response({"detail": "Course not found."}, status=status.HTTP_404_NOT_FOUND)

            try:
                private_tutoring = PrivateTutoring.objects.select_related("teacher", "course").get(
                    teacher=teacher, course=course
                )
            except PrivateTutoring.DoesNotExist:
                return Response(
                    {"detail": "No PrivateTutoring configured for the given teacher and course."},
                    status=status.HTTP_400_BAD_REQUEST
                )

        # Optional: ensure the child is enrolled in the course (uncomment if required)
        # if not Enrollment.objects.filter(student=student, course=private_tutoring.course).exists():
        #     return Response({"detail": "Student is not enrolled in the selected course."},
        #                     status=status.HTTP_400_BAD_REQUEST)

        # ---- Core booking parameters ----
        duration_hours = data.get("duration_hours", 2)
        try:
            duration_hours = int(duration_hours)
            if duration_hours <= 0:
                raise ValueError()
        except (ValueError, TypeError):
            return Response({"detail": "duration_hours must be a positive integer."},
                            status=status.HTTP_400_BAD_REQUEST)

        notes = data.get("notes", "") or ""

        # Parents usually should not set arbitrary statuses, but we allow an optional override.
        status_choice = (data.get("status") or TutoringBooking.Status.PENDING)
        valid_statuses = {c[0] for c in TutoringBooking.Status.choices}
        if status_choice not in valid_statuses:
            return Response({"detail": f"status must be one of {sorted(valid_statuses)}."},
                            status=status.HTTP_400_BAD_REQUEST)

        # ---- Price computation ----
        try:
            rate = Decimal(private_tutoring.rate_per_hour)
        except Exception:
            return Response({"detail": "Invalid rate on PrivateTutoring."},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        price = rate * Decimal(duration_hours)

        # ---- Create or Update ----
        booking_id = data.get("booking_id")

        with transaction.atomic():
            if booking_id:
                # Update flow: booking must belong to this parent/child pair (via student) and same teacher
                try:
                    booking = (
                        TutoringBooking.objects
                        .select_related("student__user", "teacher__user", "private_tutoring")
                        .get(pk=booking_id, student=student)
                    )
                except TutoringBooking.DoesNotExist:
                    return Response({"detail": "Booking not found for this parent/child."},
                                    status=status.HTTP_404_NOT_FOUND)

                # Ensure we’re not moving it to a different teacher/student silently
                if booking.teacher_id != private_tutoring.teacher_id:
                    return Response(
                        {"detail": "Cannot change booking to a different teacher."},
                        status=status.HTTP_400_BAD_REQUEST
                    )

                # Update mutable fields
                booking.private_tutoring = private_tutoring
                booking.duration_hours = duration_hours
                booking.price = price
                booking.status = status_choice
                booking.notes = notes
                booking.save()

            else:
                # Create flow
                booking = TutoringBooking.objects.create(
                    private_tutoring=private_tutoring,
                    teacher=private_tutoring.teacher,
                    student=student,
                    duration_hours=duration_hours,
                    price=price,
                    status=status_choice,
                    notes=notes,
                )

        return Response(_serialize_booking(booking),
                        status=status.HTTP_200_OK if booking_id else status.HTTP_201_CREATED)

    except Exception as e:
        payload = {
            "detail": "Error creating/updating tutoring booking.",
            "error": f"{type(e).__name__}: {e}",
        }
        if request.query_params.get("debug") in {"1", "true"} or getattr(settings, "DEBUG", False):
            payload["traceback"] = traceback.format_exc()
        return Response(payload, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def my_child_tutoring_bookings(request):
    """
    List tutoring bookings for a given child (must be linked to the authenticated parent).

    Query params:
      - student_id: <int> (required)
      - status: optional filter (pending|confirmed|completed|cancelled)
    """
    try:
        parent = _get_parent_for_user(request.user)
        if not parent:
            return Response({"detail": "Parent profile not found."}, status=status.HTTP_403_FORBIDDEN)

        student_id = request.query_params.get("student_id")
        if not student_id:
            return Response({"detail": "student_id is required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            student = StudentProfile.objects.get(pk=student_id)
        except StudentProfile.DoesNotExist:
            student = get_object_or_404_ajax(User, pk=student_id)
            if student is False:
                return Response({"detail": "Student not found."}, status=status.HTTP_404_NOT_FOUND)
            student = student.student_profile

        if not ParentChildLink.objects.filter(parent=parent, student=student).exists():
            return Response({"detail": "This student is not linked to your parent account."},
                            status=status.HTTP_403_FORBIDDEN)

        qs = TutoringBooking.objects.select_related("teacher__user", "private_tutoring").filter(student=student)
        status_filter = request.query_params.get("status")
        if status_filter:
            valid_statuses = {c[0] for c in TutoringBooking.Status.choices}
            if status_filter not in valid_statuses:
                return Response({"detail": f"status must be one of {sorted(valid_statuses)}."},
                                status=status.HTTP_400_BAD_REQUEST)
            qs = qs.filter(status=status_filter)

        return Response([_serialize_booking(b) for b in qs.order_by("-id")], status=status.HTTP_200_OK)

    except Exception as e:
        payload = {
            "detail": "Error fetching tutoring bookings.",
            "error": f"{type(e).__name__}: {e}",
        }
        if request.query_params.get("debug") in {"1", "true"} or getattr(settings, "DEBUG", False):
            payload["traceback"] = traceback.format_exc()
        return Response(payload, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


def _paginate(qs, request, default_size=10, max_size=50):
    """Simple offset pagination with page & page_size query params."""
    try:
        page = max(1, int(request.query_params.get("page", 1)))
    except Exception:
        page = 1
    try:
        page_size = min(max_size, max(1, int(request.query_params.get("page_size", default_size))))
    except Exception:
        page_size = default_size
    start = (page - 1) * page_size
    end = start + page_size
    total = qs.count()

    return {
        "results": qs[start:end],
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": (total + page_size - 1) // page_size if page_size else 1,
        },
    }


def _teacher_card_dict(pt: PrivateTutoring):
    """Flatten data for tutor cards in the UI."""
    teacher = pt.teacher
    course = pt.course
    # Modules under the course (names only)
    module_names = list(Module.objects.filter(course=course).values_list("name", flat=True)[:12])

    # Languages (names only, if you use Language model via M2M on TeacherProfile)
    language_names = list(teacher.languages.values_list("language_name", flat=True))

    # Availability -> return day strings only (Mon..Sun expectation)
    day_map = {
        "monday": "Mon", "tuesday": "Tue", "wednesday": "Wed", "thursday": "Thu",
        "friday": "Fri", "saturday": "Sat", "sunday": "Sun",
    }
    days = list(
        AvailableDay.objects.filter(private_tutoring=pt)
        .values_list("day", flat=True)
    )
    availability_days = [day_map.get(d, d) for d in days]

    # (Optional) derived/fake metrics if you don’t store them yet
    # You can replace these with real aggregates (ratings, total sessions, etc.)
    rating = 4.8
    total_sessions = TutoringBooking.objects.filter(teacher=teacher).count()

    return {
        "id": pt.id,
        "title":pt.title,
        "teacher_id": teacher.id,
        "teacher_name": teacher.user.get_full_name() or teacher.user.email,
        "course_id": course.id,
        "course": getattr(course, "title", str(course)),
        "modules": module_names,
        "rating": float(rating),
        "experience": f"{getattr(teacher, 'experience', 0)}+ years",
        "rate": f"₦{pt.rate_per_hour}/hour",
        "languages": language_names,
        "availability_days": availability_days,  # ["Mon","Wed","Fri"]
        "verified": True,
        "premiumTutor": False,
        "sessionTypes": ["One-on-One"],
        "technologies": ["Interactive Whiteboard", "Screen Sharing", "Recording"],
        "avatar": None,  # plug your avatar URL if you store it (e.g. teacher.user.avatar.url)
        "specialization": ", ".join(list(teacher.specialties.values_list("name", flat=True)[:5])),
        "totalSessions": total_sessions,
        "responseTime": "< 6 hours",
    }


def _booking_dict(b: TutoringBooking):
    """Shape booking item for 'Current / Past tutoring' lists."""
    tutor_name = b.teacher.user.get_full_name() or b.teacher.user.email
    course_title = getattr(getattr(b, "private_tutoring", None), "course", None)
    course_title = getattr(course_title, "title", str(course_title) if course_title else "Course")

    # naive status mapping to UI labels (capitalize)
    status_label = b.status.capitalize()

    # No strict times stored in TutoringBooking model; adapt if you add schedule fields.
    return {
        "id": b.id,
        "child": b.student.user.get_full_name() or b.student.user.email,
        "tutor": tutor_name,
        "subject": course_title,
        "date": (b.created_at or b.created or timezone.now()).date().isoformat(),
        "time": "—",
        "type": "One-on-One",
        "status": status_label,
        "meetingLink": None,
        "cost": f"₦{b.price}",
        "tutorAvatar": None,
        "notes": b.notes or "",
        "hasRecording": False,
        "canReschedule": b.status in [TutoringBooking.Status.PENDING, TutoringBooking.Status.CONFIRMED],
        "paymentStatus": "Paid" if b.status in [TutoringBooking.Status.CONFIRMED, TutoringBooking.Status.COMPLETED] else "Pending",
        "sessionType": "Premium",
        "duration": b.duration_hours * 60,  # minutes for UI
        "reminderSent": False,
        "actualDuration": b.duration_hours * 60 if b.status == TutoringBooking.Status.COMPLETED else None,
      }


# ---------------------------
# endpoints (READ-ONLY)
# ---------------------------

@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def tutoring_children(request):
    """
    List children linked to the authenticated parent (for the 'Select Child' dropdown).
    """
    parent = _get_parent_for_user(request.user)
    if not parent:
        return Response({"detail": "Parent profile not found."}, status=status.HTTP_403_FORBIDDEN)

    links = (
        ParentChildLink.objects
        .select_related("student__user")
        .filter(parent=parent)
        .order_by("student__user__first_name", "student__user__last_name")
    )
    data = [
        {
            "id": link.student_id,
            "name": link.student.user.get_full_name() or link.student.user.email,
            "classroom": getattr(link.student.current_classroom, "name", ""),
        }
        for link in links
    ]
    return Response(data, status=status.HTTP_200_OK)


@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def tutoring_bookings(request):
    """
    List bookings for a parent, optionally filtering by:
      - student_id: <int>
      - scope: 'upcoming' | 'past' (optional; default shows all)
      - status: one of TutoringBooking.Status (optional)
    Supports pagination via page & page_size.
    """
    parent = _get_parent_for_user(request.user)
    if not parent:
        return Response({"detail": "Parent profile not found."}, status=status.HTTP_403_FORBIDDEN)

    qs = (
        TutoringBooking.objects
        .select_related("student__user", "teacher__user", "private_tutoring__course")
        .filter(student__parent_links__parent=parent)
        .order_by("-id")
    )

    student_id = request.query_params.get("student_id")
    if student_id:
        qs = qs.filter(student_id=student_id)

    status_filter = request.query_params.get("status")
    if status_filter:
        valid_statuses = {c[0] for c in TutoringBooking.Status.choices}
        if status_filter not in valid_statuses:
            return Response({"detail": f"status must be one of {sorted(valid_statuses)}"}, status=400)
        qs = qs.filter(status=status_filter)
    scope = request.query_params.get("scope")  # upcoming | past
    if scope == "upcoming":
        qs = qs.exclude(status=TutoringBooking.Status.COMPLETED).exclude(status=TutoringBooking.Status.CANCELLED)
    elif scope == "past":
        qs = qs.filter(status__in=[TutoringBooking.Status.COMPLETED, TutoringBooking.Status.CANCELLED])

    paged = _paginate(qs, request)
    items = [_booking_dict(b) for b in paged["results"]]
    return Response({"results": items, **paged["pagination"]}, status=200)


@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def tutoring_tutors(request):
    """
    List available tutors (via PrivateTutoring rows). Optional filters:
      - course_id
      - teacher_id
    Supports pagination via page & page_size.
    """
    qs = (
        PrivateTutoring.objects
        .select_related("teacher__user", "course")
        .prefetch_related("teacher__languages", "teacher__specialties", "available_days")
        .order_by("teacher__user__first_name", "teacher__user__last_name")
    )
    course_id = request.query_params.get("course_id")
    teacher_id = request.query_params.get("teacher_id")
    if course_id:
        qs = qs.filter(course_id=course_id)
    if teacher_id:
        qs = qs.filter(teacher_id=teacher_id)
    paged = _paginate(qs, request)
    items = [_teacher_card_dict(pt) for pt in paged["results"]]
    return Response({"results": items, **paged["pagination"]}, status=200)


@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def tutoring_tutor_availability(request, private_tutoring_id: int):
    """
    Return day-of-week availability for a specific PrivateTutoring id.
    """
    try:
        pt = PrivateTutoring.objects.get(pk=private_tutoring_id)
    except PrivateTutoring.DoesNotExist:
        return Response({"detail": "PrivateTutoring not found."}, status=404)

    day_map = {
        "monday": "Mon", "tuesday": "Tue", "wednesday": "Wed", "thursday": "Thu",
        "friday": "Fri", "saturday": "Sat", "sunday": "Sun",
    }
    days = list(AvailableDay.objects.filter(private_tutoring=pt).values_list("day", flat=True))
    return Response({"days": [day_map.get(d, d) for d in days]}, status=200)


@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def tutoring_stats(request):
    """
    Small dashboard numbers needed at the page bottom:
      - total_tutoring = upcoming + past
      - upcoming_count
      - hours_completed (sum of completed bookings' duration_hours)
      - average_rating (placeholder unless you store ratings)
      - active_tutors (count of distinct PrivateTutoring rows or teachers)
    Optional filter: student_id
    """
    parent = _get_parent_for_user(request.user)
    if not parent:
        return Response({"detail": "Parent profile not found."}, status=403)

    qs = TutoringBooking.objects.filter(student__parent_links__parent=parent)
    student_id = request.query_params.get("student_id")
    if student_id:
        qs = qs.filter(student_id=student_id)

    upcoming = qs.exclude(status=TutoringBooking.Status.COMPLETED).exclude(status=TutoringBooking.Status.CANCELLED).count()
    past = qs.filter(status__in=[TutoringBooking.Status.COMPLETED, TutoringBooking.Status.CANCELLED]).count()
    total = upcoming + past

    # hours completed (sum duration_hours for COMPLETED)
    completed_minutes = sum([
        (b.duration_hours or 0) * 60
        for b in qs.filter(status=TutoringBooking.Status.COMPLETED)
    ])
    hours_completed = round(completed_minutes / 60.0, 1)

    # average rating — placeholder (replace with real ratings if you store them)
    # To keep consistent with your UI, compute from completed sessions if you later add a rating field.
    average_rating = 4.7 if past else 0.0

    active_tutors = (
        PrivateTutoring.objects.values("teacher_id")
        .distinct()
        .count()
    )

    return Response({
        "total_tutoring": total,
        "upcoming_count": upcoming,
        "hours_completed": hours_completed,
        "average_rating": average_rating,
        "active_tutors": active_tutors,
    }, status=200)



@api_view(["GET", "POST", "PATCH", "DELETE"])
@authentication_classes([SessionTokenAuthentication])
@permission_classes([HasAPIKey])
def teacher_tutoring_bookings(request):
    """
    Endpoint for teacher tutoring bookings page.
    - GET: List PrivateTutoring offerings (?tab=private) or TutoringBooking (?tab=upcoming|past)
           with pagination (?page=1, ?limit=3)
    - POST: Create PrivateTutoring offering (with available_days)
    - PATCH: Update booking status (body: {"id": int, "status": str}) or PrivateTutoring status (body: {"id": int, "status": str})
    - DELETE: Delete booking or PrivateTutoring (?id=int, ?tab=private|upcoming)
    """
    try:
        user = request.user
        teacher = _get_teacher_for_user(user)
        if not teacher:
            return Response({"detail": "Teacher profile not found."}, status=status.HTTP_404_NOT_FOUND)

        org, err = _resolve_org(request)
        if err:
            return err

        if not _is_org_admin_or_teacher(request, org):
            return Response({"detail": "Insufficient permissions."}, status=status.HTTP_403_FORBIDDEN)

        if request.method == "GET":
            tab = request.query_params.get("tab", "upcoming")
            if tab not in ["upcoming", "past", "private"]:
                return Response({"detail": "Invalid tab parameter. Use 'upcoming', 'past', or 'private'."},
                               status=status.HTTP_400_BAD_REQUEST)

            # Pagination parameters
            page = _to_int(request.query_params.get("page", 1))
            limit = _to_int(request.query_params.get("limit", 3))
            start = (page - 1) * limit

            if tab == "private":
                # Handle PrivateTutoring for "Private Sessions" tab
                qs = PrivateTutoring.objects.filter(teacher=teacher).select_related("course")
                total = qs.count()
                qs = qs[start:start + limit]
                serializer = PrivateTutoringSerializer(qs, many=True)
            else:
                # Handle TutoringBooking for "Current Private Session" (upcoming) or "Past Sessions" (past)
                qs = TutoringBooking.objects.filter(teacher=teacher).select_related(
                    "student__user", "private_tutoring__course"
                )
                now = timezone.now()
                if tab == "upcoming":
                    qs = qs.filter(
                        Q(status__in=["pending", "confirmed"]) &
                        Q(completed_date__isnull=True) &
                        Q(created_at__gte=now - timedelta(days=1))
                    ).order_by("created_at")
                else:  # past
                    qs = qs.filter(
                        Q(status__in=["completed", "cancelled"]) |
                        Q(completed_date__lte=now)
                    ).order_by("-completed_date", "-created_at")
                total = qs.count()
                qs = qs[start:start + limit]
                serializer = TutoringBookingTeacherSerializer(qs, many=True)

            return Response({
                "results": serializer.data,
                "total": total,
                "page": page,
                "pages": (total + limit - 1) // limit if limit > 0 else 0
            })

        elif request.method == "POST":
            # Create PrivateTutoring (for "Private Sessions" tab)
            data = request.data.copy()
            data["teacher"] = teacher.id
            serializer = PrivateTutoringSerializer(data=data)
            if serializer.is_valid():
                serializer.save()
                return Response(serializer.data, status=status.HTTP_201_CREATED)
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        elif request.method == "PATCH":
            # Update status for TutoringBooking or PrivateTutoring
            tab = request.query_params.get("tab", "upcoming")
            if tab not in ["upcoming", "private"]:
                return Response({"detail": "PATCH only supported for 'upcoming' or 'private' tabs."},
                               status=status.HTTP_400_BAD_REQUEST)

            item_id = request.data.get("id")
            if not item_id:
                return Response({"detail": "id required."}, status=status.HTTP_400_BAD_REQUEST)

            status_new = request.data.get("status")
            if not status_new:
                return Response({"detail": "status required."}, status=status.HTTP_400_BAD_REQUEST)

            if tab == "private":
                # Update PrivateTutoring status
                item = get_object_or_404(PrivateTutoring, id=item_id, teacher=teacher)
                if status_new not in ["Active", "Inactive"]:
                    return Response({"detail": "Invalid status. Use 'Active' or 'Inactive'."},
                                   status=status.HTTP_400_BAD_REQUEST)
                item.status = status_new
                item.save()
                return Response(PrivateTutoringSerializer(item).data)
            else:
                # Update TutoringBooking status
                item = get_object_or_404(TutoringBooking, id=item_id, teacher=teacher)
                if status_new not in dict(TutoringBooking.Status.choices):
                    return Response({"detail": "Invalid status."}, status=status.HTTP_400_BAD_REQUEST)
                item.status = status_new
                if status_new == "completed":
                    item.completed_date = timezone.now()
                item.save()
                return Response(TutoringBookingTeacherSerializer(item).data)

        elif request.method == "DELETE":
            # Delete PrivateTutoring or TutoringBooking
            tab = request.query_params.get("tab", "upcoming")
            if tab not in ["upcoming", "private"]:
                return Response({"detail": "DELETE only supported for 'upcoming' or 'private' tabs."},
                               status=status.HTTP_400_BAD_REQUEST)

            item_id = request.query_params.get("id")
            if not item_id:
                return Response({"detail": "id required."}, status=status.HTTP_400_BAD_REQUEST)

            if tab == "private":
                # Delete PrivateTutoring
                item = get_object_or_404(PrivateTutoring, id=item_id, teacher=teacher)
                if TutoringBooking.objects.filter(private_tutoring=item, status__in=["pending", "confirmed"]).exists():
                    return Response({"detail": "Cannot delete PrivateTutoring with active bookings."},
                                   status=status.HTTP_400_BAD_REQUEST)
                item.available_days.all().delete()
                item.delete()
            else:
                # Delete TutoringBooking
                item = get_object_or_404(TutoringBooking, id=item_id, teacher=teacher)
                if item.status == "completed":
                    return Response({"detail": "Cannot delete completed booking."},
                                   status=status.HTTP_400_BAD_REQUEST)
                item.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)

        return Response({"detail": "Method not allowed."}, status=status.HTTP_405_METHOD_NOT_ALLOWED)

    except Exception as e:
        traceback.print_exc()
        return Response(
            {"detail": f"An unexpected error occurred: {str(e)}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )








