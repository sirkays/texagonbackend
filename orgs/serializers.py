# classrooms/serializers.py
from rest_framework import serializers
from orgs.models import Organization
from academics.models import Classroom, StudentProfile,TeacherProfile,Subject
from learning.models import Course
from orgs.models import Organization, OrganizationMembership
from core.utils import (StatusLiteral, _resolve_org, _status_from_user_membership,_apply_status_to_user_membership)



class StudentReadSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    email = serializers.EmailField()
    classroom = serializers.CharField(allow_null=True)
    admissionNo = serializers.CharField(source="admission_no", allow_blank=True)
    status = serializers.CharField()
    avatar = serializers.CharField(allow_null=True, required=False)  # <-- added


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


# ---- SERIALIZERS ----

class TeacherListSerializer(serializers.ModelSerializer):
    # flat UI fields
    id = serializers.IntegerField(read_only=True)
    name = serializers.SerializerMethodField()
    email = serializers.EmailField(source="user.email", read_only=True)
    experience = serializers.IntegerField()
    bio = serializers.CharField()
    specialties = serializers.SerializerMethodField()
    courses = serializers.IntegerField(source="courses_count", read_only=True)
    avatarUrl = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()

    class Meta:
        model = TeacherProfile
        fields = ["id", "name", "email", "experience", "bio", "specialties", "courses", "avatarUrl", "status"]

    def get_name(self, obj):
        u = obj.user
        return (u.get_full_name() or u.email or str(u.pk))

    def get_specialties(self, obj):
        return list(obj.specialties.values_list("name", flat=True))

    def get_avatarUrl(self, obj):
        u = obj.user
        return u.avatar.url if getattr(u, "avatar", None) else ""

    def get_status(self, obj):
        org = obj.organization
        mem = OrganizationMembership.objects.filter(
            user=obj.user, organization=org, role=OrganizationMembership.Role.TEACHER
        ).first()
        return _status_from_user_membership(obj.user, mem)


class TeacherWriteSerializer(serializers.Serializer):
    """
    Input serializer for create/update.
    Accepts optional avatar upload (multipart) and optional status chip.
    """
    # User fields
    name = serializers.CharField(required=False, allow_blank=True)
    email = serializers.EmailField(required=True)
    phone = serializers.CharField(required=False, allow_blank=True)
    # Profile fields
    bio = serializers.CharField(required=False, allow_blank=True)
    experience = serializers.IntegerField(required=True, min_value=0)
    specialties = serializers.ListField(
        child=serializers.CharField(), required=False
    )
    # status chip (active/inactive/suspended)
    status = serializers.ChoiceField(choices=["active", "inactive", "suspended"], required=False)
    # avatar upload (admin can update even if UI didn’t show an input)
    avatar = serializers.ImageField(required=False, allow_null=True)

    def validate_specialties(self, value):
        # Free text from UI -> must exist in Subject for this org
        org: Organization = self.context["org"]
        names = [v.strip() for v in value if v.strip()]
        existing = set(Subject.objects.filter(organization=org, name__in=names).values_list("name", flat=True))
        missing = [n for n in names if n not in existing]
        if missing:
            raise serializers.ValidationError(
                f"Unknown specialties for this organization: {', '.join(missing)}"
            )
        return names


class TeacherDetailSerializer(TeacherListSerializer):
    """Same shape as list, but can add more fields later if needed."""
    pass

