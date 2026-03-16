from django.contrib import admin
from .models import ProjectCategory, ProjectTag, StudentProject


@admin.register(ProjectCategory)
class ProjectCategoryAdmin(admin.ModelAdmin):
    list_display  = ("name", "slug", "colour")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(ProjectTag)
class ProjectTagAdmin(admin.ModelAdmin):
    list_display  = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(StudentProject)
class StudentProjectAdmin(admin.ModelAdmin):
    list_display  = (
        "title", "student_name", "student_school",
        "category", "difficulty", "is_featured", "is_published", "completed_at",
    )
    list_filter   = ("is_published", "is_featured", "category", "difficulty")
    search_fields = ("title", "student_name", "student_school", "excerpt")
    prepopulated_fields = {"slug": ("title",)}
    filter_horizontal   = ("tags",)
    readonly_fields     = ("created_at", "updated_at")

    fieldsets = (
        ("Identity", {
            "fields": ("title", "slug", "subtitle", "excerpt", "description"),
        }),
        ("Taxonomy", {
            "fields": ("category", "tags", "difficulty"),
        }),
        ("Media", {
            "fields": (
                "thumbnail",
                "thumb_emoji", "thumb_gradient",
                "image_1", "image_2", "image_3",
                "video_url",
            ),
        }),
        ("Links", {
            "fields": ("demo_url", "repo_url"),
        }),
        ("Student", {
            "fields": ("student_name", "student_school", "student_photo", "student_user"),
        }),
        ("Publishing", {
            "fields": ("is_published", "is_featured", "completed_at", "created_at", "updated_at"),
        }),
    )