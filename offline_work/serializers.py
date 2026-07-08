from rest_framework import serializers
from .models import OfflinePracticalWork, OfflinePracticalScore


class OfflinePracticalWorkSerializer(serializers.ModelSerializer):
    course_name = serializers.CharField(source="course.title", read_only=True)
    course_code = serializers.CharField(source="course.code", read_only=True, default="")
    score_count = serializers.SerializerMethodField()
    graded_count = serializers.SerializerMethodField()
    pending_count = serializers.SerializerMethodField()
    average_score = serializers.SerializerMethodField()
    assessment_type_display = serializers.CharField(
        source="get_assessment_type_display", read_only=True
    )

    class Meta:
        model = OfflinePracticalWork
        fields = [
            "id",
            "title",
            "course",
            "course_name",
            "course_code",
            "academic_session",
            "assessment_type",
            "assessment_type_display",
            "max_score",
            "conducted_at",
            "description",
            "visibility",
            "score_count",
            "graded_count",
            "pending_count",
            "average_score",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def get_score_count(self, obj):
        return obj.scores.count()

    def get_graded_count(self, obj):
        return obj.scores.filter(score__isnull=False).count()

    def get_pending_count(self, obj):
        return obj.scores.filter(score__isnull=True).count()

    def get_average_score(self, obj):
        from django.db.models import Avg
        result = obj.scores.filter(score__isnull=False).aggregate(avg=Avg("score"))
        avg = result.get("avg")
        if avg is None:
            return None
        return round(float(avg), 2)


class OfflinePracticalScoreSerializer(serializers.ModelSerializer):
    student_name = serializers.SerializerMethodField()
    student_email = serializers.SerializerMethodField()
    classroom_name = serializers.SerializerMethodField()
    classroom_id = serializers.SerializerMethodField()

    class Meta:
        model = OfflinePracticalScore
        fields = [
            "id",
            "opw",
            "student",
            "student_name",
            "student_email",
            "classroom_name",
            "classroom_id",
            "score",
            "feedback",
            "recorded_at",
        ]
        read_only_fields = ["id", "recorded_at"]

    def get_student_name(self, obj):
        try:
            user = obj.student.user
            full = f"{user.first_name} {user.last_name}".strip()
            return full or user.email
        except Exception:
            return str(obj.student_id)

    def get_student_email(self, obj):
        try:
            return obj.student.user.email
        except Exception:
            return ""

    def get_classroom_name(self, obj):
        try:
            return obj.student.classroom.name if obj.student.classroom else None
        except Exception:
            return None

    def get_classroom_id(self, obj):
        try:
            return obj.student.classroom_id
        except Exception:
            return None


class OPWStudentSerializer(serializers.Serializer):
    """
    Lightweight serializer used by the student-list endpoint.
    Returns enrolled students alongside their current OPW score (if any).
    """
    student_id = serializers.IntegerField()
    student_name = serializers.CharField()
    student_email = serializers.CharField()
    classroom_id = serializers.IntegerField(allow_null=True)
    classroom_name = serializers.CharField(allow_null=True)
    score_id = serializers.IntegerField(allow_null=True)
    score = serializers.DecimalField(max_digits=7, decimal_places=2, allow_null=True)
    feedback = serializers.CharField(allow_null=True)
    recorded_at = serializers.DateTimeField(allow_null=True)
