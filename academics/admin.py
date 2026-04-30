from django.contrib import admin
from .models import (Language, Classroom, Subject, StudentProfile, 
    TeacherProfile, ParentProfile, ParentChildLink,OrganizationCertificateSignatures,EnrollmentCertificate,
    StudentEnrollmentCertificateApproval
    )
from django.utils import timezone
from django.utils.html import format_html

@admin.register(StudentProfile)
class StudentProfileAdmin(admin.ModelAdmin):
    # Use method names instead of user__firstname
    list_display = (
        "user", 
        "get_firstname", 
        "get_lastname", 
        "organization", 
        "current_classroom", 
        "admission_no", 
        "dob", 
        "created_at"
    )
    list_filter = ("organization", "current_classroom")
    search_fields = ("user__username", "user__email", "admission_no", "organization__name", "current_classroom__name")
    autocomplete_fields = ("user", "organization", "current_classroom")

    # Define the methods to access related data
    @admin.display(ordering='user__first_name', description='First Name')
    def get_firstname(self, obj):
        return obj.user.first_name

    @admin.display(ordering='user__last_name', description='Last Name')
    def get_lastname(self, obj):
        return obj.user.last_name


@admin.register(Classroom)
class ClassroomAdmin(admin.ModelAdmin):
    list_display = ("name", "organization", "code", "created_at")
    list_filter = ("organization",)
    search_fields = ("name", "code", "organization__name")
    filter_horizontal = ("teachers",)

@admin.register(Subject)
class SubjectAdmin(admin.ModelAdmin):
    list_display = ("name", "organization", "code", "created_at")
    list_filter = ("organization",)
    search_fields = ("name", "code", "organization__name")



@admin.register(TeacherProfile)
class TeacherProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "organization", "created_at")
    list_filter = ("organization",)
    search_fields = ("user__username", "user__email", "organization__name")
    autocomplete_fields = ("user", "organization")
    filter_horizontal = ("specialties",)



@admin.register(ParentProfile)
class ParentProfileAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "organization",
        "organization_subscription",
        "last_billed_at",
        "created_at",
    )
    list_filter = (
        "organization",
        "organization_subscription",
        ("last_billed_at", admin.DateFieldListFilter),
    )
    search_fields = ("user__username", "user__email", "organization__name")
    autocomplete_fields = ("user", "organization", "organization_subscription")
    ordering = ("-created_at",)


@admin.register(ParentChildLink)
class ParentChildLinkAdmin(admin.ModelAdmin):
    list_display = ("parent", "student", "relationship", "created_at")
    search_fields = ("parent__user__username", "parent__user__email", "student__user__username", "student__user__email")
    autocomplete_fields = ("parent", "student")



@admin.register(Language)
class LanguageAdmin(admin.ModelAdmin):
    list_display = ('language_name', 'active')
    list_filter = ('active',)
    search_fields = ('language_name',)





@admin.register(EnrollmentCertificate)
class EnrollmentCertificateAdmin(admin.ModelAdmin):
    # --- List page ---
    list_display = (
        "id",
        "number",
        "status",
        "organization",
        "student_name",
        "course",
        "acquired_at",
        "downloadable_at_display",
        "can_download_display",
        "pdf_link",
        "issued_by_user",
        "revoked_at",
        "created_at",
    )
    list_filter = (
        "status",
        "organization",
        "course",
        "issued_by_user",
        ("acquired_at", admin.DateFieldListFilter),
        ("created_at", admin.DateFieldListFilter),
        ("revoked_at", admin.DateFieldListFilter),
    )
    search_fields = (
        "number",
        "verification_token",
        "student__user__email",
        "student__user__first_name",
        "student__user__last_name",
        "course__name",
        "enrollment__id",
    )
    autocomplete_fields = ("organization", "enrollment", "student", "course", "issued_by_user")
    ordering = ("-acquired_at", "-created_at")

    # --- Detail page ---
    readonly_fields = (
        "number",
        "verification_token",
        "created_at",
        "updated_at",
        "downloadable_at_display",
        "can_download_display",
        "pdf_link",
    )
    fieldsets = (
        ("Links", {
            "fields": ("organization", "enrollment", "student", "course")
        }),
        ("Certificate", {
            "fields": ("status", "title", "description", "acquired_at", "download_after_days")
        }),
        ("Files", {
            "fields": ("pdf_file", "pdf_link")
        }),
        ("Issuer", {
            "fields": ("issued_by_user", "meta")
        }),
        ("Revocation", {
            "fields": ("revoked_at", "revoked_reason"),
            "classes": ("collapse",),
        }),
        ("Computed", {
            "fields": ("downloadable_at_display", "can_download_display"),
            "classes": ("collapse",),
        }),
        ("System", {
            "fields": ("number", "verification_token", "created_at", "updated_at"),
            "classes": ("collapse",),
        }),
    )

    # --- Admin actions ---
    actions = ("mark_issued", "mark_revoked")

    @admin.action(description="Mark selected certificates as ISSUED (clear revocation)")
    def mark_issued(self, request, queryset):
        now = timezone.now()
        updated = queryset.update(
            status=EnrollmentCertificate.Status.ISSUED,
            revoked_at=None,
            revoked_reason="",
            updated_at=now,
        )
        self.message_user(request, f"{updated} certificate(s) marked as issued.")

    @admin.action(description="Mark selected certificates as REVOKED (set revoked_at=now)")
    def mark_revoked(self, request, queryset):
        now = timezone.now()
        updated = queryset.update(
            status=EnrollmentCertificate.Status.REVOKED,
            revoked_at=now,
            updated_at=now,
        )
        self.message_user(request, f"{updated} certificate(s) revoked.")

    # --- Display helpers ---
    @admin.display(description="Student")
    def student_name(self, obj: EnrollmentCertificate):
        try:
            u = obj.student.user
            return (u.get_full_name() or u.email or str(u.pk)).strip()
        except Exception:
            return str(obj.student_id)

    @admin.display(description="Downloadable At")
    def downloadable_at_display(self, obj: EnrollmentCertificate):
        # You mentioned downloadable_at is a property in your codebase, so we treat it as read-only.
        # If not present, compute using acquired_at + download_after_days.
        try:
            val = getattr(obj, "downloadable_at", None)
            if val:
                return timezone.localtime(val)
        except Exception:
            pass

        try:
            return timezone.localtime(obj.acquired_at + timezone.timedelta(days=int(obj.download_after_days or 0)))
        except Exception:
            return "-"

    @admin.display(description="Can Download", boolean=True)
    def can_download_display(self, obj: EnrollmentCertificate):
        # If you have a can_download property, use it; otherwise compute.
        try:
            val = getattr(obj, "can_download", None)
            if isinstance(val, bool):
                return val
        except Exception:
            pass

        try:
            if obj.status != EnrollmentCertificate.Status.ISSUED:
                return False
            downloadable_at = obj.acquired_at + timezone.timedelta(days=int(obj.download_after_days or 0))
            return timezone.now() >= downloadable_at
        except Exception:
            return False

    @admin.display(description="PDF")
    def pdf_link(self, obj: EnrollmentCertificate):
        if not obj.pdf_file:
            return "-"
        try:
            return format_html('<a href="{}" target="_blank" rel="noopener">Open PDF</a>', obj.pdf_file.url)
        except Exception:
            return "(file set)"

    # --- Save safeguards ---
    def save_model(self, request, obj: EnrollmentCertificate, form, change):
        """
        Keep revocation fields consistent with status.
        Also auto-fill issued_by_user if issuing and not set.
        """
        # If admin revokes, set revoked_at if missing
        if obj.status == EnrollmentCertificate.Status.REVOKED:
            if not obj.revoked_at:
                obj.revoked_at = timezone.now()
        else:
            # If issuing, clear revoked fields
            obj.revoked_at = None
            obj.revoked_reason = ""

        # If issuing and issuer empty, record the admin user (optional but nice)
        if obj.status == EnrollmentCertificate.Status.ISSUED and not obj.issued_by_user:
            if request and request.user and request.user.is_authenticated:
                obj.issued_by_user = request.user

        super().save_model(request, obj, form, change)




@admin.register(OrganizationCertificateSignatures)
class OrganizationCertificateSignaturesAdmin(admin.ModelAdmin):
    list_display = (
        "organization",
        "director_1_name",
        "director_1_preview",
        "director_2_name",
        "director_2_preview",
        "updated_at",
    )
    search_fields = ("organization__name", "director_1_name", "director_2_name")
    readonly_fields = ("director_1_preview", "director_2_preview", "created_at", "updated_at")
    autocomplete_fields = ("organization",)

    fieldsets = (
        ("Organization", {"fields": ("organization",)}),
        ("Director 1", {"fields": ("director_1_name", "director_1_title", "director_1_signature", "director_1_preview")}),
        ("Director 2", {"fields": ("director_2_name", "director_2_title", "director_2_signature", "director_2_preview")}),
        ("Meta", {"fields": ("meta",)}),
        ("System", {"fields": ("created_at", "updated_at")}),
    )

    def director_1_preview(self, obj):
        if not obj.director_1_signature:
            return "-"
        return format_html('<img src="{}" style="height:48px;border:1px solid #ddd;" />', obj.director_1_signature.url)

    def director_2_preview(self, obj):
        if not obj.director_2_signature:
            return "-"
        return format_html('<img src="{}" style="height:48px;border:1px solid #ddd;" />', obj.director_2_signature.url)




@admin.register(StudentEnrollmentCertificateApproval)
class StudentEnrollmentCertificateApprovalAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "certificate",
        "user",
        "user_type",
        "approval",
        "created_at",
        "updated_at",
    )

    list_filter = (
        "approval",
        "user_type",
        "created_at",
    )

    search_fields = (
        "certificate__number",
        "certificate__student__user__email",
        "user__email",
    )

    autocomplete_fields = (
        "certificate",
        "user",
    )

    readonly_fields = (
        "created_at",
        "updated_at",
    )

    fieldsets = (
        (
            "Approval Details",
            {
                "fields": (
                    "certificate",
                    "user",
                    "user_type",
                    "approval",
                )
            },
        ),
        (
            "Timestamps",
            {
                "fields": (
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )

    ordering = ("-created_at",)

    def has_delete_permission(self, request, obj=None):
        """
        Optional: prevent deletion of approvals to keep audit history.
        Remove this if deletions are acceptable.
        """
        return request.user.is_superuser
