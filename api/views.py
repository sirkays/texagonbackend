import json
import traceback
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import authenticate
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from rest_framework import status, viewsets
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.response import Response

from rest_framework_api_key.permissions import HasAPIKey

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
    LiveSessionSerializer, TutoringBookingSerializer,
    ProductCategorySerializer, ProductSerializer, OrderSerializer, OrderItemSerializer,
    SubscriptionPlanSerializer, OrganizationSubscriptionSerializer, SubscriptionInvoiceSerializer, SubscriptionPaymentSerializer,
    NotificationSerializer,
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
from store.models import ProductCategory, Product, Order, OrderItem
from billing.models import (
    SubscriptionPlan, OrganizationSubscription, SubscriptionInvoice, SubscriptionPayment,
)
from notifications.models import Notification

from core.utils import _get_teacher_for_user, _get_student_for_user

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
    queryset = Lesson.objects.all()
    serializer_class = LessonSerializer

class MaterialViewSet(APIKeySessionViewSet):
    queryset = Material.objects.all()
    serializer_class = MaterialSerializer

class BookmarkViewSet(APIKeySessionViewSet):
    queryset = Bookmark.objects.all()
    serializer_class = BookmarkSerializer

class NoteViewSet(APIKeySessionViewSet):
    queryset = Note.objects.all()
    serializer_class = NoteSerializer


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


class ProductCategoryViewSet(APIKeySessionViewSet):
    queryset = ProductCategory.objects.all()
    serializer_class = ProductCategorySerializer

class ProductViewSet(APIKeySessionViewSet):
    queryset = Product.objects.all()
    serializer_class = ProductSerializer

class OrderViewSet(APIKeySessionViewSet):
    queryset = Order.objects.all()
    serializer_class = OrderSerializer

class OrderItemViewSet(APIKeySessionViewSet):
    queryset = OrderItem.objects.all()
    serializer_class = OrderItemSerializer


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
            return Response({"detail": "Student not found."}, status=status.HTTP_404_NOT_FOUND)

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
