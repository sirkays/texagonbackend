from django.contrib.auth import authenticate
from rest_framework import viewsets, status
from rest_framework.response import Response
from rest_framework.decorators import api_view, permission_classes, authentication_classes
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
from academics.models import Classroom, Subject, StudentProfile, TeacherProfile, ParentProfile, ParentChildLink
from learning.models import Course, Enrollment, Module, Lesson, Material, Bookmark, Note
from assessments.models import Test, Question, Choice, TestAttempt, Assignment, Submission
from attendance.models import AttendanceSession, AttendanceRecord
from gamification.models import Badge, BadgeAward, PointTransaction, Streak
from live.models import LiveSession, TutoringBooking
from store.models import ProductCategory, Product, Order, OrderItem
from billing.models import SubscriptionPlan, OrganizationSubscription, SubscriptionInvoice, SubscriptionPayment
from notifications.models import Notification


@api_view(["POST"])
@permission_classes([HasAPIKey])
@authentication_classes([])
def login_view(request):
    username = request.data.get("username")
    password = request.data.get("password")
    hours_valid = int(request.data.get("hours_valid") or 24)

    if not username or not password:
        return Response({"detail": "username and password are required."}, status=status.HTTP_400_BAD_REQUEST)

    user = authenticate(request, username=username, password=password)
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
