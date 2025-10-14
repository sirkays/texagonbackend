# ide/admin.py
from django.contrib import admin
from django.db import models
from django.utils.html import format_html
from django.utils.text import Truncator

from .models import CodeSnippet, CodeSubmission, CodeComment,CodeFile


# ---------- Inlines ----------
class CodeCommentInline(admin.TabularInline):
    model = CodeComment
    extra = 0
    fields = ("author", "author_role", "message", "created_at")
    readonly_fields = ("created_at",)
    raw_id_fields = ("author",)


# ---------- Base mixins ----------
class MonospaceTextMixin:
    """Render large TextFields with a monospaced textarea in admin."""
    formfield_overrides = {
        models.TextField: {
            "widget": admin.widgets.AdminTextareaWidget(attrs={"style": "font-family:monospace; width:100%; min-height:180px;"})
        }
    }


# ---------- CodeSnippet ----------
@admin.register(CodeSnippet)
class CodeSnippetAdmin(MonospaceTextMixin, admin.ModelAdmin):
    date_hierarchy = "created_at"
    list_display = (
        "id",
        "student_name",
        "lesson_title",
        "language",
        "code_preview",
        "created_at",
        "updated_at",
    )
    list_filter = ("language", "created_at", "updated_at")
    search_fields = (
        "title",
        "language",
        "code_text",
        "student__user__email",
        "student__user__first_name",
        "student__user__last_name",
        "lesson__name",
        "lesson__module__course__teacher__user__email",
    )
    readonly_fields = ("created_at", "updated_at")
    raw_id_fields = ("student", "lesson")

    fieldsets = (
        (None, {
            "fields": ("student", "lesson", "title", "language", "code_text", "meta")
        }),
        ("Timestamps", {
            "fields": ("created_at", "updated_at"),
        }),
    )

    @admin.display(description="Student")
    def student_name(self, obj):
        u = getattr(getattr(obj.student, "user", None), "email", None)
        if u:
            return u
        return f"Student #{obj.student_id}"

    @admin.display(description="Lesson")
    def lesson_title(self, obj):
        return getattr(obj.lesson, "name", "") or f"Lesson #{obj.lesson_id}" if obj.lesson_id else "-"

    @admin.display(description="Code", ordering="code_text")
    def code_preview(self, obj):
        return Truncator(obj.code_text).chars(60)


# ---------- CodeSubmission ----------
@admin.register(CodeSubmission)
class CodeSubmissionAdmin(MonospaceTextMixin, admin.ModelAdmin):
    date_hierarchy = "created_at"
    list_display = (
        "id",
        "lesson_title",
        "student_name",
        "language",
        "status",
        "score",
        "graded_by_name",
        "graded_at",
        "created_at",
    )
    list_filter = ("status", "language", "graded_at", "created_at")
    search_fields = (
        "code_text",
        "lesson__name",
        "student__user__email",
        "student__user__first_name",
        "student__user__last_name",
        "graded_by__user__email",
    )
    readonly_fields = ("created_at", "updated_at")
    raw_id_fields = ("student", "lesson", "graded_by")
    inlines = [CodeCommentInline]

    fieldsets = (
        ("Submission", {
            "fields": ("lesson", "student", "language", "code_text", "status")
        }),
        ("Grading", {
            "fields": ("score", "feedback", "correction_code", "graded_by", "graded_at")
        }),
        ("Timestamps", {
            "fields": ("created_at", "updated_at"),
        }),
    )

    actions = ("mark_as_graded", "mark_as_revised")

    @admin.display(description="Lesson")
    def lesson_title(self, obj):
        return getattr(obj.lesson, "name", "") or f"Lesson #{obj.lesson_id}"

    @admin.display(description="Student")
    def student_name(self, obj):
        u = getattr(getattr(obj.student, "user", None), "email", None)
        return u or f"Student #{obj.student_id}"

    @admin.display(description="Graded by")
    def graded_by_name(self, obj):
        if obj.graded_by and getattr(obj.graded_by, "user", None):
            return obj.graded_by.user.email or f"Teacher #{obj.graded_by_id}"
        return "-"

    @admin.action(description="Mark selected submissions as GRADED")
    def mark_as_graded(self, request, queryset):
        updated = queryset.update(status=CodeSubmission.Status.GRADED)
        self.message_user(request, f"{updated} submission(s) marked as graded.")

    @admin.action(description="Mark selected submissions as REVISED")
    def mark_as_revised(self, request, queryset):
        updated = queryset.update(status=CodeSubmission.Status.REVISED)
        self.message_user(request, f"{updated} submission(s) marked as revised.")


# ---------- CodeComment ----------
@admin.register(CodeComment)
class CodeCommentAdmin(MonospaceTextMixin, admin.ModelAdmin):
    date_hierarchy = "created_at"
    list_display = ("id", "submission_id", "author_email", "author_role", "message_preview", "created_at")
    list_filter = ("author_role", "created_at")
    search_fields = (
        "message",
        "submission__lesson__name",
        "author__email",
        "author__first_name",
        "author__last_name",
    )
    readonly_fields = ("created_at", "updated_at")
    raw_id_fields = ("submission", "author")

    fields = ("submission", "author", "author_role", "message", "created_at", "updated_at")

    @admin.display(description="Author")
    def author_email(self, obj):
        return getattr(obj.author, "email", None) or f"User #{obj.author_id}"

    @admin.display(description="Message")
    def message_preview(self, obj):
        return Truncator(obj.message).chars(60)

admin.site.register(CodeFile) 