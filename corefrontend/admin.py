from django.contrib import admin
from .models import OurProgram, TutorApplication,ApplicationsDashboardAccess


@admin.register(ApplicationsDashboardAccess)
class ApplicationsDashboardAccessAdmin(admin.ModelAdmin):
    list_display = (
        'user',
        'can_view_dashboard',
        'can_export_csv',
        'is_active',
        'created_at',
        'updated_at',
    )
    list_filter = (
        'can_view_dashboard',
        'can_export_csv',
        'is_active',
        'created_at',
        'updated_at',
    )
    search_fields = (
        'user__username',
        'user__first_name',
        'user__last_name',
        'user__email',
        'notes',
    )
    readonly_fields = (
        'created_at',
        'updated_at',
    )
    autocomplete_fields = ('user',)
    list_select_related = ('user',)
    ordering = ('-created_at',)

    fieldsets = (
        ('User', {
            'fields': ('user',)
        }),
        ('Access Control', {
            'fields': (
                'can_view_dashboard',
                'can_export_csv',
                'is_active',
            )
        }),
        ('Notes', {
            'fields': ('notes',)
        }),
        ('Timestamps', {
            'fields': (
                'created_at',
                'updated_at',
            )
        }),
    )

@admin.register(OurProgram)
class OurProgramAdmin(admin.ModelAdmin):
    list_display = ("name", "active")
    list_filter = ("active",)
    search_fields = ("name",)


@admin.register(TutorApplication)
class TutorApplicationAdmin(admin.ModelAdmin):
    list_display = (
        "email",
        "full_name",
        "position",
        "status",
        "current_step",
        "state_residence",
        "created_at",
        "updated_at",
    )
    list_filter = (
        "position",
        "status",
        "current_step",
        "gender",
        "education_level",
        "nysc_status",
        "has_taught",
        "has_laptop",
        "has_internet",
        "attend_training",
        "willing_to_relocate",
        "work_fulltime",
        "service_agreement",
        "created_at",
    )
    search_fields = (
        "email",
        "full_name",
        "phone",
        "state_residence",
        "state_origin",
        "nationality",
        "institution",
        "course_of_study",
    )
    readonly_fields = ("created_at", "updated_at")

    fieldsets = (
        ("Application Info", {
            "fields": (
                "email",
                "position",
                "status",
                "current_step",
                "created_at",
                "updated_at",
            )
        }),
        ("Step 1 · Personal", {
            "fields": (
                "full_name",
                "dob",
                "gender",
                "phone",
                "address",
                "state_residence",
                "state_origin",
                "nationality",
                "identification",
                "id_upload",
            )
        }),
        ("Step 2 · Education", {
            "fields": (
                "education_level",
                "course_of_study",
                "institution",
                "graduation_year",
                "nysc_status",
                "degree_fields",
                "other_degree",
                "cv_upload",
            )
        }),
        ("Step 3 · Skills", {
            "fields": (
                "skills",
                "years_experience",
                "has_taught",
                "teaching_location",
                "has_laptop",
                "has_internet",
            )
        }),
        ("Step 4 · Availability", {
            "fields": (
                "attend_training",
                "willing_to_relocate",
                "work_fulltime",
                "start_date",
                "preferred_states",
            )
        }),
        ("Step 5 · Screening", {
            "fields": (
                "why_techxagon",
                "why_select",
                "future_robotics",
                "service_agreement",
                "video_upload",
            )
        }),
    )

    def get_queryset(self, request):
        return super().get_queryset(request)

    @admin.display(description="ID Uploaded", boolean=True)
    def has_id_upload(self, obj):
        return bool(obj.id_upload)

    @admin.display(description="CV Uploaded", boolean=True)
    def has_cv_upload(self, obj):
        return bool(obj.cv_upload)

    @admin.display(description="Video Uploaded", boolean=True)
    def has_video_upload(self, obj):
        return bool(obj.video_upload)
    




