from django.contrib import admin
from .models import (Language, Classroom, Subject, StudentProfile, 
    TeacherProfile, ParentProfile, ParentChildLink)

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

@admin.register(StudentProfile)
class StudentProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "organization", "current_classroom", "admission_no", "dob", "created_at")
    list_filter = ("organization", "current_classroom")
    search_fields = ("user__username", "user__email", "admission_no", "organization__name", "current_classroom__name")
    autocomplete_fields = ("user", "organization", "current_classroom")

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
