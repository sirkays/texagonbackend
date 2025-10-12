# classrooms/serializers.py
from rest_framework import serializers
from orgs.models import Organization
from academics.models import Classroom, StudentProfile
from learning.models import Course

class StudentReadSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    email = serializers.EmailField()
    classroom = serializers.CharField(allow_null=True)
    admissionNo = serializers.CharField(source="admission_no", allow_blank=True)
    status = serializers.CharField()

class StudentWriteSerializer(serializers.Serializer):
    """
    Used for create/update from the modal.
    If 'id' is provided -> update, else -> create.
    """
    id = serializers.IntegerField(required=False)
    name = serializers.CharField()
    email = serializers.EmailField()
    classroom = serializers.CharField(allow_blank=True, required=False)  # classroom name (e.g., "Grade 10A")
    admissionNo = serializers.CharField(required=False, allow_blank=True)
    status = serializers.ChoiceField(choices=["active", "inactive", "suspended"], default="active")


class ClassroomBaseSerializer(serializers.ModelSerializer):
    class Meta:
        model = Classroom
        fields = ["id", "name", "code"]

class ClassroomListSerializer(ClassroomBaseSerializer):
    students = serializers.IntegerField(source="students_count", read_only=True)
    teachers = serializers.IntegerField(source="teachers_count", read_only=True)
    courses  = serializers.IntegerField(source="courses_count", read_only=True)

    class Meta(ClassroomBaseSerializer.Meta):
        fields = ClassroomBaseSerializer.Meta.fields + ["students", "teachers", "courses"]



class ClassroomWriteSerializer(ClassroomBaseSerializer):
    """Used for create/update."""

    def validate(self, attrs):
        request = self.context["request"]
        # Attach org in the view; we just ensure unique per org+name
        return attrs

class StudentMiniSerializer(serializers.ModelSerializer):
    full_name = serializers.SerializerMethodField()

    class Meta:
        model = StudentProfile
        fields = ["id", "admission_no", "full_name"]

    def get_full_name(self, obj):
        u = obj.user
        return (getattr(u, "get_full_name", lambda: "")() or u.email or u.pk)
