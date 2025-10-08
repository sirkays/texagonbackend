from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    login_view, logout_view,
    OrganizationViewSet, OrganizationMembershipViewSet, AcademicSessionViewSet,
    ClassroomViewSet, SubjectViewSet, StudentProfileViewSet, TeacherProfileViewSet,
    ParentProfileViewSet, ParentChildLinkViewSet,
    CourseViewSet, EnrollmentViewSet, ModuleViewSet, LessonViewSet, MaterialViewSet,
    BookmarkViewSet, NoteViewSet,
    TestViewSet, QuestionViewSet, ChoiceViewSet, TestAttemptViewSet, AssignmentViewSet, SubmissionViewSet,
    AttendanceSessionViewSet, AttendanceRecordViewSet,
    BadgeViewSet, BadgeAwardViewSet, PointTransactionViewSet, StreakViewSet,
    LiveSessionViewSet, TutoringBookingViewSet,
    ProductCategoryViewSet, ProductViewSet, OrderViewSet, OrderItemViewSet,
    SubscriptionPlanViewSet, OrganizationSubscriptionViewSet, SubscriptionInvoiceViewSet, SubscriptionPaymentViewSet,
    NotificationViewSet,upsert_tutoring_booking, my_child_tutoring_bookings,
    tutoring_children,
    tutoring_bookings,
    tutoring_tutors,
    tutoring_tutor_availability,
    tutoring_stats,
)

router = DefaultRouter()
router.register(r"organizations", OrganizationViewSet)
router.register(r"organization-memberships", OrganizationMembershipViewSet)
router.register(r"academic-sessions", AcademicSessionViewSet)
router.register(r"classrooms", ClassroomViewSet)
router.register(r"subjects", SubjectViewSet)
router.register(r"students", StudentProfileViewSet)
router.register(r"teachers", TeacherProfileViewSet)
router.register(r"parents", ParentProfileViewSet)
router.register(r"parent-links", ParentChildLinkViewSet)
router.register(r"courses", CourseViewSet)
router.register(r"enrollments", EnrollmentViewSet)
router.register(r"modules", ModuleViewSet)
router.register(r"lessons", LessonViewSet)
router.register(r"materials", MaterialViewSet)
router.register(r"bookmarks", BookmarkViewSet)
router.register(r"notes", NoteViewSet)
router.register(r"tests", TestViewSet)
router.register(r"questions", QuestionViewSet)
router.register(r"choices", ChoiceViewSet)
router.register(r"test-attempts", TestAttemptViewSet)
router.register(r"assignments", AssignmentViewSet)
router.register(r"submissions", SubmissionViewSet)
router.register(r"attendance-sessions", AttendanceSessionViewSet)
router.register(r"attendance-records", AttendanceRecordViewSet)
router.register(r"badges", BadgeViewSet)
router.register(r"badge-awards", BadgeAwardViewSet)
router.register(r"point-transactions", PointTransactionViewSet)
router.register(r"streaks", StreakViewSet)
router.register(r"live-sessions", LiveSessionViewSet)
router.register(r"tutoring-bookings", TutoringBookingViewSet)
router.register(r"product-categories", ProductCategoryViewSet)
router.register(r"products", ProductViewSet)
router.register(r"orders", OrderViewSet)
router.register(r"order-items", OrderItemViewSet)
router.register(r"subscription-plans", SubscriptionPlanViewSet)
router.register(r"org-subscriptions", OrganizationSubscriptionViewSet)
router.register(r"invoices", SubscriptionInvoiceViewSet)
router.register(r"payments", SubscriptionPaymentViewSet)
router.register(r"notifications", NotificationViewSet)

urlpatterns = [
    path("auth/login/", login_view, name="api-login"),
    path("auth/logout/", logout_view, name="api-logout"),

    path("tutor/tutoring/book/", upsert_tutoring_booking, name="upsert_tutoring_booking"),
    path("tutor/tutoring-bookings/", my_child_tutoring_bookings, name="my_child_tutoring_bookings"),
    ####### #####

    path("tutor/tutoring/children/", tutoring_children, name="tutoring_children"),
    path("tutor/tutoring/bookings/", tutoring_bookings, name="tutoring_bookings"),
    path("tutor/tutoring/tutors/", tutoring_tutors, name="tutoring_tutors"),
    path("tutor/tutoring/tutors/<int:private_tutoring_id>/availability/", tutoring_tutor_availability, name="tutoring_tutor_availability"),
    path("tutor/tutoring/stats/", tutoring_stats, name="tutoring_stats"),
    path("", include(router.urls)),
]