# classrooms/serializers.py
from rest_framework import serializers
from orgs.models import Organization
from academics.models import Classroom, StudentProfile,TeacherProfile,Subject,ParentProfile, ParentChildLink 
from learning.models import Course
from orgs.models import Organization, OrganizationMembership
from core.utils import (
    StatusLiteral, _resolve_org, _status_from_user_membership,_apply_status_to_user_membership,
    _avatar_url_for,_get_or_create_parent_membership,_is_admin
)
from django.db import transaction
from accounts.models import User

class SubjectWriteSerializer(serializers.ModelSerializer):
    """Minimal write serializer (model has only name/code)."""
    class Meta:
        model = Subject
        fields = ["id", "name", "code"]


class SubjectListItemSerializer(serializers.ModelSerializer):
    """Read serializer with the counters your cards need."""
    courses = serializers.IntegerField(read_only=True)
    teachers = serializers.IntegerField(read_only=True)
    students = serializers.IntegerField(read_only=True)

    class Meta:
        model = Subject
        fields = ["id", "name", "code", "courses", "teachers", "students"]


class ParentListSerializer(serializers.ModelSerializer):
    id = serializers.IntegerField(read_only=True)
    name = serializers.SerializerMethodField()
    email = serializers.SerializerMethodField()
    phone = serializers.SerializerMethodField()
    children_count = serializers.IntegerField(read_only=True)
    subscription_status = serializers.SerializerMethodField()
    avatar_url = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField(help_text="active | inactive | suspended")

    class Meta:
        model = ParentProfile
        fields = [
            "id", "name", "email", "phone",
            "children_count", "subscription_status",
            "avatar_url", "status",
            "address", "created_at", "updated_at",
        ]

    def get_name(self, obj):
        u = obj.user
        return (getattr(u, "get_full_name", lambda: "")() or u.email or str(u.pk))

    def get_email(self, obj):
        return obj.user.email

    def get_phone(self, obj):
        return obj.user.phone

    def get_subscription_status(self, obj):
        sub = obj.organization_subscription
        return getattr(sub, "status", None)

    def get_avatar_url(self, obj):
        return _avatar_url_for(obj.user, self.context["request"])

    def get_status(self, obj) -> StatusLiteral:
        membership = OrganizationMembership.objects.filter(
            user=obj.user, organization=obj.organization, role=OrganizationMembership.Role.PARENT
        ).first()
        return _status_from_user_membership(obj.user, membership)


class ParentDetailSerializer(ParentListSerializer):
    children = serializers.SerializerMethodField()

    class Meta(ParentListSerializer.Meta):
        fields = ParentListSerializer.Meta.fields + ["children"]

    def get_children(self, obj):
        # Use mini serializer with full name
        qs = StudentProfile.objects.select_related("user").filter(parent_links__parent=obj).distinct()
        return StudentMiniSerializer(qs, many=True).data


class ParentWriteSerializer(serializers.Serializer):
    """
    Used for create/update.
    Allows admin to update avatar on the related User.
    """
    # User fields
    name = serializers.CharField(required=False, allow_blank=True)
    email = serializers.EmailField(required=False)  # required on create
    phone = serializers.CharField(required=False, allow_blank=True)
    # ParentProfile fields
    address = serializers.CharField(required=False, allow_blank=True)
    organization_subscription_id = serializers.IntegerField(required=False, allow_null=True)
    # UI status chip (optional)
    status = serializers.ChoiceField(choices=["active", "inactive", "suspended"], required=False)
    # Avatar (admin only)
    avatar = serializers.ImageField(required=False, allow_null=True)

    def _is_creating(self) -> bool:
        """
        True only for actual POST creates (no instance bound).
        This avoids misfires during GET/OPTIONS/schema, etc.
        """
        req = self.context.get("request")
        if req and req.method.upper() == "POST" and self.instance is None:
            return True
        # keep supporting your existing flag, but don't rely on it
        return bool(self.context.get("creating", False))

    def validate(self, attrs):
        # only enforce 'email' when creating
        if self._is_creating() and not attrs.get("email"):
            raise serializers.ValidationError({"email": "Email is required."})

        # only enforce avatar rule if an avatar was provided
        request = self.context.get("request")
        if "avatar" in attrs and attrs["avatar"] is not None:
            if not _is_admin(request):
                raise serializers.ValidationError({"avatar": "Only admins can update avatar."})
        return attrs

    def create(self, validated_data):
        request = self.context["request"]
        org: Organization = self.context["org"]

        email = validated_data["email"].lower().strip()
        name = validated_data.get("name", "")
        phone = validated_data.get("phone", "")
        address = validated_data.get("address", "")
        avatar: UploadedFile | None = validated_data.get("avatar")
        status_value: StatusLiteral | None = validated_data.get("status")

        org_sub_id = validated_data.get("organization_subscription_id")
        org_sub = None
        if org_sub_id:
            org_sub = OrganizationSubscription.objects.filter(id=org_sub_id, organization=org).first()

        with transaction.atomic():
            # Create or attach user
            user, created = User.objects.get_or_create(email=email, defaults={"is_active": True})
            # Fill in names if given
            if name:
                # naive "split" to first_name/last_name if you use Django's standard fields
                try:
                    first, *rest = name.strip().split()
                    user.first_name = first
                    user.last_name = " ".join(rest)
                except Exception:
                    pass
            if phone:
                user.phone = phone
            if avatar and _is_admin(request):
                user.avatar = avatar
            user.save()

            # membership
            membership = _get_or_create_parent_membership(user, org)

            # status mapping (if UI provided)
            if status_value:
                _apply_status_to_user_membership(status_value, user, membership)
                user.save(update_fields=["is_active"])
                membership.save(update_fields=["is_active"])

            parent = ParentProfile.objects.create(
                user=user,
                organization=org,
                organization_subscription=org_sub,
                address=address,
            )
        return parent

    def update(self, instance: ParentProfile, validated_data):
        request = self.context["request"]
        org: Organization = self.context["org"]

        name = validated_data.get("name", None)
        phone = validated_data.get("phone", None)
        address = validated_data.get("address", None)
        org_sub_id = validated_data.get("organization_subscription_id", None)
        avatar: UploadedFile | None = validated_data.get("avatar", None)
        status_value: StatusLiteral | None = validated_data.get("status")

        with transaction.atomic():
            user = instance.user
            if name is not None:
                try:
                    first, *rest = name.strip().split()
                    user.first_name = first
                    user.last_name = " ".join(rest)
                except Exception:
                    pass
            if phone is not None:
                user.phone = phone
            if avatar is not None:
                if not _is_admin(request):
                    raise serializers.ValidationError({"avatar": "Only admins can update avatar."})
                user.avatar = avatar
            user.save()

            membership = _get_or_create_parent_membership(user, org)
            if status_value:
                _apply_status_to_user_membership(status_value, user, membership)
                user.save(update_fields=["is_active"])
                membership.save(update_fields=["is_active"])

            if org_sub_id is not None:
                if org_sub_id:
                    org_sub = OrganizationSubscription.objects.filter(id=org_sub_id, organization=org).first()
                    instance.organization_subscription = org_sub
                else:
                    instance.organization_subscription = None

            if address is not None:
                instance.address = address

            instance.save()

        return instance


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

