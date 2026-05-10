# codeide/admin.py
from django.contrib import admin
from django.db import models
from django.utils.html import format_html
from django.utils.text import Truncator

from .models import CodeSnippet, CodeProject, ProjectFile, CodeComment, CodeFile, Folder


class MonospaceTextMixin:
    formfield_overrides = {
        models.TextField: {
            "widget": admin.widgets.AdminTextareaWidget(attrs={"style": "font-family:monospace; width:100%; min-height:180px;"})
        }
    }


# ---------- CodeSnippet ----------
@admin.register(CodeSnippet)
class CodeSnippetAdmin(MonospaceTextMixin, admin.ModelAdmin):
    date_hierarchy = "created_at"
    list_display = ("id", "student_name", "lesson_title", "language", "code_preview", "created_at", "updated_at")
    list_filter = ("language", "created_at", "updated_at")
    search_fields = ("title", "language", "code_text", "student__user__email", "student__user__first_name", "student__user__last_name", "lesson__name")
    readonly_fields = ("created_at", "updated_at")
    raw_id_fields = ("student", "lesson")

    @admin.display(description="Student")
    def student_name(self, obj):
        return getattr(getattr(obj.student, "user", None), "email", None) or f"Student #{obj.student_id}"

    @admin.display(description="Lesson")
    def lesson_title(self, obj):
        return getattr(obj.lesson, "name", "") or f"Lesson #{obj.lesson_id}" if obj.lesson_id else "-"

    @admin.display(description="Code")
    def code_preview(self, obj):
        return Truncator(obj.code_text).chars(60)


# ---------- ProjectFile Inline ----------
class ProjectFileInline(admin.TabularInline):
    model = ProjectFile
    extra = 0
    fields = ("path", "language", "code_preview", "correction_preview")
    readonly_fields = ("code_preview", "correction_preview")

    @admin.display(description="Code")
    def code_preview(self, obj):
        return Truncator(obj.code_text).chars(80)

    @admin.display(description="Correction")
    def correction_preview(self, obj):
        return Truncator(obj.correction_code).chars(80) if obj.correction_code else "-"


# ---------- CodeComment Inline ----------
class CodeCommentInline(admin.TabularInline):
    model = CodeComment
    extra = 0
    fields = ("author", "author_role", "message", "created_at")
    readonly_fields = ("created_at",)
    raw_id_fields = ("author",)


# ---------- CodeProject ----------
@admin.register(CodeProject)
class CodeProjectAdmin(admin.ModelAdmin):
    date_hierarchy = "created_at"
    list_display = ("id", "title", "student_name", "lesson_title", "status", "score", "file_count", "graded_at", "created_at")
    list_filter = ("status", "graded_at", "created_at")
    search_fields = ("title", "student__user__email", "student__user__first_name", "student__user__last_name", "lesson__name")
    readonly_fields = ("created_at", "updated_at")
    raw_id_fields = ("student", "lesson", "graded_by")
    inlines = [ProjectFileInline, CodeCommentInline]

    @admin.display(description="Student")
    def student_name(self, obj):
        return getattr(getattr(obj.student, "user", None), "email", None) or f"Student #{obj.student_id}"

    @admin.display(description="Lesson")
    def lesson_title(self, obj):
        return getattr(obj.lesson, "name", "") or f"Lesson #{obj.lesson_id}"

    @admin.display(description="Files")
    def file_count(self, obj):
        return obj.files.count()


# ---------- ProjectFile ----------
@admin.register(ProjectFile)
class ProjectFileAdmin(MonospaceTextMixin, admin.ModelAdmin):
    list_display = ("id", "project", "path", "language", "created_at")
    list_filter = ("language", "created_at")
    search_fields = ("path", "project__title")
    raw_id_fields = ("project",)


# ---------- CodeComment ----------
@admin.register(CodeComment)
class CodeCommentAdmin(MonospaceTextMixin, admin.ModelAdmin):
    date_hierarchy = "created_at"
    list_display = ("id", "project_id", "author_email", "author_role", "message_preview", "created_at")
    list_filter = ("author_role", "created_at")
    search_fields = ("message", "author__email")
    readonly_fields = ("created_at", "updated_at")
    raw_id_fields = ("project", "author")

    @admin.display(description="Author")
    def author_email(self, obj):
        return getattr(obj.author, "email", None) or f"User #{obj.author_id}"

    @admin.display(description="Message")
    def message_preview(self, obj):
        return Truncator(obj.message).chars(60)


# ---------- CodeFile ----------
@admin.register(CodeFile)
class CodeFileAdmin(admin.ModelAdmin):
    autocomplete_fields = ['student', 'lesson']
    list_display = ['id', 'created_at', 'updated_at', 'student', 'lesson', 'folder', 'label', 'file']
    list_filter = ['created_at', 'updated_at', 'student', 'lesson', 'folder']
    search_fields = ['label', 'original_name', 'content_type']


@admin.register(Folder)
class FolderAdmin(admin.ModelAdmin):
    autocomplete_fields = ['student']
    list_display = ['id', 'created_at', 'updated_at', 'student', 'parent', 'name']
    list_filter = ['created_at', 'updated_at', 'student', 'parent']
    search_fields = ['name']