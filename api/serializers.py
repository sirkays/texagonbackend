from decimal import Decimal
from rest_framework import serializers

from orgs.models import (
    Organization,
    OrganizationMembership,
    AcademicSession,
)
from academics.models import (
    Classroom,
    Subject,
    StudentProfile,
    TeacherProfile,
    ParentProfile,
    ParentChildLink,
    Language
)
from learning.models import (
    Course,
    Enrollment,
    Module,
    Lesson,
    Material,
    Bookmark,
    Note,
    EnrollmentAssessmentConfig,
)
from assessments.models import (
    Test,
    Question,
    Choice,
    TestAttempt,
    Assignment,
    Submission,
    SubmissionComment,
)
from attendance.models import (
    AttendanceSession,
    AttendanceRecord,
)
from gamification.models import (
    Badge,
    BadgeAward,
    PointTransaction,
    Streak,
)
from live.models import (
    LiveSession,
    TutoringBooking,
    PrivateTutoring, AvailableDay
)

from billing.models import (
    SubscriptionPlan,
    OrganizationSubscription,
    SubscriptionInvoice,
    SubscriptionPayment,
)
from notifications.models import Notification
from live.models import (
    PrivateTutoring,
    AvailableDay,
    TutoringBooking,
    LiveSession, TutoringBooking,
)
from .models import SessionToken




class OrganizationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = "__all__"


class OrganizationMembershipSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrganizationMembership
        fields = "__all__"


class AcademicSessionSerializer(serializers.ModelSerializer):
    class Meta:
        model = AcademicSession
        fields = "__all__"


class ClassroomSerializer(serializers.ModelSerializer):
    class Meta:
        model = Classroom
        fields = "__all__"


class SubjectSerializer(serializers.ModelSerializer):
    class Meta:
        model = Subject
        fields = "__all__"


class LanguageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Language
        fields = "__all__"


class StudentProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = StudentProfile
        fields = "__all__"


class TeacherProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = TeacherProfile
        fields = "__all__"


class ParentProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = ParentProfile
        fields = "__all__"


class ParentChildLinkSerializer(serializers.ModelSerializer):
    class Meta:
        model = ParentChildLink
        fields = "__all__"


class CourseSerializer(serializers.ModelSerializer):
    class Meta:
        model = Course
        fields = "__all__"


class EnrollmentAssessmentConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = EnrollmentAssessmentConfig
        fields = "__all__"
        read_only_fields = ["organization"]

class EnrollmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Enrollment
        fields = "__all__"


class ModuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Module
        fields = "__all__"


class LessonSerializer(serializers.ModelSerializer):
    course_id = serializers.IntegerField(source="module.course.id", read_only=True)
    course_name = serializers.CharField(source="module.course.name", read_only=True)
    module_name = serializers.CharField(source="module.name", read_only=True)

    class Meta:
        model = Lesson
        fields = "__all__"


class MaterialSerializer(serializers.ModelSerializer):
    class Meta:
        model = Material
        fields = "__all__"


class BookmarkSerializer(serializers.ModelSerializer):
    class Meta:
        model = Bookmark
        fields = ["id", "student", "lesson", "note", "position_seconds", "created_at", "updated_at"]
        read_only_fields = ["id", "student", "created_at", "updated_at"]

class NoteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Note
        fields = ["id","title" ,"student", "lesson", "content", "is_private", "created_at", "updated_at"]
        read_only_fields = ["id", "student", "created_at", "updated_at"]


class TestSerializer(serializers.ModelSerializer):
    class Meta:
        model = Test
        fields = "__all__"


class QuestionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Question
        fields = "__all__"


class ChoiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Choice
        fields = "__all__"


class TestAttemptSerializer(serializers.ModelSerializer):
    class Meta:
        model = TestAttempt
        fields = "__all__"


class AssignmentSerializer(serializers.ModelSerializer):
    course_type = serializers.CharField(source="course.course_type", read_only=True)
    course_name = serializers.CharField(source="course.title", read_only=True)
    submission_count = serializers.SerializerMethodField(read_only=True)
    graded_count = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Assignment
        fields = "__all__"

    def get_submission_count(self, obj):
        return obj.submissions.count()

    def get_graded_count(self, obj):
        return obj.submissions.filter(score__isnull=False).count()

    def to_representation(self, instance):
        rep = super().to_representation(instance)
        if instance.lesson:
            rep['lesson_details'] = LessonSerializer(instance.lesson, context=self.context).data
        else:
            rep['lesson_details'] = None
        return rep


class SubmissionSerializer(serializers.ModelSerializer):
    student_name = serializers.SerializerMethodField(read_only=True)

    def get_student_name(self, obj):
        try:
            user = obj.student.user
            full = f"{user.first_name} {user.last_name}".strip()
            return full or user.email
        except Exception:
            return str(obj.student_id)

    class Meta:
        model = Submission
        fields = "__all__"

class SubmissionCommentSerializer(serializers.ModelSerializer):
    class Meta:
        model = SubmissionComment
        fields = "__all__"

class AttendanceSessionSerializer(serializers.ModelSerializer):
    class Meta:
        model = AttendanceSession
        fields = "__all__"


class AttendanceRecordSerializer(serializers.ModelSerializer):
    class Meta:
        model = AttendanceRecord
        fields = "__all__"


class BadgeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Badge
        fields = "__all__"


class BadgeAwardSerializer(serializers.ModelSerializer):
    class Meta:
        model = BadgeAward
        fields = "__all__"


class PointTransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = PointTransaction
        fields = "__all__"


class StreakSerializer(serializers.ModelSerializer):
    class Meta:
        model = Streak
        fields = "__all__"


class LiveSessionSerializer(serializers.ModelSerializer):
    class Meta:
        model = LiveSession
        fields = "__all__"


class TutoringBookingSerializer(serializers.ModelSerializer):
    class Meta:
        model = TutoringBooking
        fields = "__all__"



class SubscriptionPlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = SubscriptionPlan
        fields = "__all__"


class OrganizationSubscriptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrganizationSubscription
        fields = "__all__"


class SubscriptionInvoiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = SubscriptionInvoice
        fields = "__all__"


class SubscriptionPaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = SubscriptionPayment
        fields = "__all__"


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = "__all__"


class SessionTokenSerializer(serializers.ModelSerializer):
    class Meta:
        model = SessionToken
        fields = ["key", "created_at", "expires_at", "is_active", "meta"]
        read_only_fields = ["key", "created_at", "expires_at", "is_active", "meta"]




class AvailableDaySerializer(serializers.ModelSerializer):
    class Meta:
        model = AvailableDay
        fields = ['day']  # Only include 'day' since private_tutoring is handled in create
        extra_kwargs = {
            'day': {'required': True}
        }

class PrivateTutoringSerializer(serializers.ModelSerializer):
    available_days = AvailableDaySerializer(many=True, required=False)
    courseName = serializers.SerializerMethodField()

    class Meta:
        model = PrivateTutoring
        fields = '__all__'

    def get_courseName(self, obj):
        return obj.course.name if obj.course else ''

    def create(self, validated_data):
        days_data = validated_data.pop('available_days', [])
        pt = PrivateTutoring.objects.create(**validated_data)
        for day_data in days_data:
            AvailableDay.objects.create(private_tutoring=pt, **day_data)
        return pt

    def update(self, instance, validated_data):
        days_data = validated_data.pop('available_days', None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        if days_data is not None:
            instance.available_days.all().delete()
            for day_data in days_data:
                AvailableDay.objects.create(private_tutoring=instance, **day_data)
        return instance

class TutoringBookingTeacherSerializer(serializers.ModelSerializer):
    student_name = serializers.SerializerMethodField()
    course_name = serializers.SerializerMethodField()

    def get_student_name(self, obj):
        return obj.student.user.get_full_name() or obj.student.user.email

    def get_course_name(self, obj):
        return obj.private_tutoring.course.name if obj.private_tutoring else ''

    class Meta:
        model = TutoringBooking
        fields = [
            'id', 'private_tutoring', 'teacher', 'student', 'student_name',
            'duration_hours', 'price', 'status', 'notes', 'completed_date',
            'course_name', 'created_at'
        ]

class CancelTutoringBookingSerializer(serializers.Serializer):
    booking_id = serializers.IntegerField()
