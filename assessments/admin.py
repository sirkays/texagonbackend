from django.contrib import admin
from .models import Test, Question, Choice, TestAttempt, Assignment, Submission, TestAnswer

class QuestionInline(admin.TabularInline):
    model = Question
    extra = 0

@admin.register(Test)
class TestAdmin(admin.ModelAdmin):
    list_display = ("title", "course", "visibility", "duration_minutes", "start_at", "end_at", "created_at")
    list_filter = ("visibility", "course")
    search_fields = ("title", "course__name")
    autocomplete_fields = ("course",)
    inlines = [QuestionInline]

class ChoiceInline(admin.TabularInline):
    model = Choice
    extra = 0

@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ("test", "order", "qtype", "points", "created_at")
    list_filter = ("qtype", "test")
    search_fields = ("test__title", "body")
    autocomplete_fields = ("test",)
    inlines = [ChoiceInline]

@admin.register(Choice)
class ChoiceAdmin(admin.ModelAdmin):
    list_display = ("question", "order", "text", "is_correct", "created_at")
    list_filter = ("is_correct", "question__test")
    search_fields = ("question__test__title", "text")
    autocomplete_fields = ("question",)

@admin.register(TestAttempt)
class TestAttemptAdmin(admin.ModelAdmin):
    list_display = ("test", "student", "started_at", "submitted_at", "score", "status", "created_at")
    list_filter = ("status", "test")
    search_fields = ("test__title", "student__user__username")
    autocomplete_fields = ("test", "student")

class SubmissionInline(admin.TabularInline):
    model = Submission
    extra = 0
    autocomplete_fields = ("student",)

@admin.register(Assignment)
class AssignmentAdmin(admin.ModelAdmin):
    list_display = ("title", "course", "due_at", "created_at")
    list_filter = ("course",)
    search_fields = ("title", "course__name")
    autocomplete_fields = ("course",)
    inlines = [SubmissionInline]

@admin.register(Submission)
class SubmissionAdmin(admin.ModelAdmin):
    list_display = ("assignment", "student", "submitted_at", "score", "created_at")
    list_filter = ("assignment",)
    search_fields = ("assignment__title", "student__user__username")
    autocomplete_fields = ("assignment", "student")


@admin.register(TestAnswer)
class TestAnswerAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "attempt",
        "student_name",
        "question",
        "selected_choice",
        "awarded_points",
        "is_auto_graded",
        "created_at",
    )
    list_filter = ("is_auto_graded", "question__qtype", "attempt__test")
    search_fields = (
        "attempt__student__user__first_name",
        "attempt__student__user__last_name",
        "attempt__test__title",
        "question__body",
        "answer_text",
    )
    raw_id_fields = ("attempt", "question", "selected_choice")
    readonly_fields = ("created_at", "updated_at")

    def student_name(self, obj):
        return obj.attempt.student.user.get_full_name()
    student_name.admin_order_field = "attempt__student__user__last_name"
    student_name.short_description = "Student"
