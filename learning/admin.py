from django.contrib import admin
from .models import Course, Enrollment, Module, Lesson, Material, Bookmark, Note,ModuleCategory,CoursePassCriteria

class EnrollmentInline(admin.TabularInline):
    model = Enrollment
    extra = 0
    autocomplete_fields = ("student",)

class ModuleInline(admin.TabularInline):
    model = Module
    extra = 0

@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = ("name", "organization", "subject", "classroom", "teacher", "is_active", "created_at")
    list_filter = ("organization", "subject", "classroom", "teacher", "is_active")
    search_fields = ("name", "organization__name", "subject__name", "classroom__name", "teacher__user__username")
    autocomplete_fields = ("organization", "subject", "classroom", "teacher")
    inlines = [ModuleInline, EnrollmentInline]

class LessonInline(admin.TabularInline):
    model = Lesson
    extra = 0

@admin.register(Module)
class ModuleAdmin(admin.ModelAdmin):
    list_display = ("name", "course", "order", "created_at")
    list_filter = ("course",)
    search_fields = ("name", "course__name")
    autocomplete_fields = ("course",)
    inlines = [LessonInline]

@admin.register(Lesson)
class LessonAdmin(admin.ModelAdmin):
    list_display = ("name", "module", "order", "content_type", "duration_seconds", "created_at")
    list_filter = ("content_type", "module__course",)
    search_fields = ("name", "module__course__name", "module__name")
    autocomplete_fields = ("module",)

@admin.register(Enrollment)
class EnrollmentAdmin(admin.ModelAdmin):
    list_display = ("student", "course", "status","leaderboard_season", "progress_pct", "created_at")
    list_filter = ("status", "course")
    search_fields = ("student__user__username", "course__name", "leaderboard_season__name")
    autocomplete_fields = ("student", "course")

@admin.register(Material)
class MaterialAdmin(admin.ModelAdmin):
    list_display = ("title", "organization", "owner", "kind", "is_public", "created_at")
    list_filter = ("organization", "kind", "is_public")
    search_fields = ("title", "organization__name", "owner__username")
    autocomplete_fields = ("organization", "owner")

@admin.register(Bookmark)
class BookmarkAdmin(admin.ModelAdmin):
    list_display = ("student", "lesson", "position_seconds", "created_at")
    list_filter = ("lesson__module__course",)
    search_fields = ("student__user__username", "lesson__name")
    autocomplete_fields = ("student", "lesson")

@admin.register(Note)
class NoteAdmin(admin.ModelAdmin):
    list_display = ("student", "lesson", "is_private", "created_at")
    list_filter = ("is_private", "lesson__module__course")
    search_fields = ("student__user__username", "lesson__name", "content")
    autocomplete_fields = ("student", "lesson")


@admin.register(ModuleCategory)
class ModuleCategoryAdmin(admin.ModelAdmin):
    list_display = ['id', 'created_at', 'updated_at', 'name', 'active']
    list_filter = ['created_at', 'updated_at', 'active']
    search_fields = ['name']


@admin.register(CoursePassCriteria)
class CoursePassCriteriaAdmin(admin.ModelAdmin):
    autocomplete_fields = ['course']
    list_display = ['id', 'course', 'no_of_cbt', 'no_of_code_submission', 'total_pass_mark_cbt', 'total_pass_mark_code']
    list_filter = ['course']
