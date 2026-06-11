from django.utils import timezone
from rest_framework import serializers
from assessments.models import TestAttempt, Test
from academics.models import StudentProfile
from .models import Test


class StudentProfileMiniSerializer(serializers.ModelSerializer):
    full_name = serializers.SerializerMethodField()
    is_exclude = serializers.SerializerMethodField()

    class Meta:
        model = StudentProfile
        fields = ("id", "admission_no", "dob", "full_name", "is_exclude")

    def get_full_name(self, obj):
        u = obj.user
        return f"{u.first_name} {u.last_name}".strip()

    def get_is_exclude(self, obj):
        test = self.context.get("test")
        if not test:
            return False
        return obj.id in self.context.get("excluded_ids", set())


class TestMiniSerializer(serializers.ModelSerializer):
    # No need for 'source' here; DRF automatically maps course_id
    course_id = serializers.IntegerField(read_only=True)
    course_name = serializers.CharField(source="course.name", read_only=True)

    class Meta:
        model = Test
        fields = [
            "id",
            "title",
            "duration_minutes",
            "total_marks",
            "visibility",
            "start_at",
            "end_at",
            "course_id",
            "course_name",
            "require_browser_code",
            "show_score",
        ]
        read_only_fields = fields


class TestAttemptSerializer(serializers.ModelSerializer):
    # Compact info about the test (read-only)
    test = TestMiniSerializer(read_only=True)

    # Also expose test_id directly for convenience
    test_id = serializers.IntegerField(source="test.id", read_only=True)

    # Useful computed flags/fields
    is_submitted = serializers.SerializerMethodField()
    is_graded = serializers.SerializerMethodField()
    is_open_now = serializers.SerializerMethodField()

    class Meta:
        model = TestAttempt
        fields = [
            "id",
            "test_id",
            "test",
            "student",        # will render the student id; mark write-only if you never want to expose it
            "started_at",
            "submitted_at",
            "score",
            "status",
            "is_submitted",
            "is_graded",
            "is_open_now",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "test_id",
            "test",
            "student",
            "started_at",
            "submitted_at",
            "score",
            "status",
            "created_at",
            "updated_at",
        ]

    def get_is_submitted(self, obj):
        return bool(obj.submitted_at) or obj.status in {"submitted", "graded"}

    def get_is_graded(self, obj):
        return obj.status == "graded"

    def get_is_open_now(self, obj):
        """
        Mirrors the filter used in the endpoint:
        (start_at is null or <= now) AND (end_at is null or >= now)
        """
        now = timezone.now()
        t = obj.test
        start_ok = (t.start_at is None) or (t.start_at <= now)
        end_ok = (t.end_at is None) or (t.end_at >= now)
        return start_ok and end_ok
