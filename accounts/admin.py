from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.db.models import Q, Count
from .models import AdminAccess, User, EmailOTP, EmailChangeRequest
from django import forms
from orgs.models import Organization
from django.utils.translation import gettext_lazy as _

class AccountTypeFilter(admin.SimpleListFilter):
    title = _('account type')
    parameter_name = 'account_type'

    def lookups(self, request, model_admin):
        return (
            ('student', _('Student Profile')),
            ('teacher', _('Teacher Profile')),
            ('parent', _('Parent Profile')),
        )

    def queryset(self, request, queryset):
        if self.value() == 'student':
            return queryset.filter(student_profile__isnull=False)
        if self.value() == 'teacher':
            return queryset.filter(teacher_profile__isnull=False)
        if self.value() == 'parent':
            return queryset.filter(parent_profile__isnull=False)
        return queryset
@admin.register(User)
class UserAdmin(BaseUserAdmin):
    # Show extra fields
    list_display = (
        "email",
        "first_name",
        "last_name",
        "phone",
        "primary_org",
        "is_generated",   # ✅ added
        "is_staff",
        "is_active",
    )

    list_filter = (
        "is_staff",
        "is_superuser",
        "is_active",
        "is_generated",   # ✅ added
        "groups",
        "primary_org",
        AccountTypeFilter,
    )

    search_fields = ("email", "first_name", "last_name", "phone")
    ordering = ("email",)

    fieldsets = (
        (None, {"fields": ("password",)}),
        ("Personal info", {
            "fields": (
                "username",
                "first_name",
                "last_name",
                "email",
                "phone",
                "avatar",
                "primary_org",
                "is_generated",   # ✅ added
            )
        }),
        ("Permissions", {
            "fields": (
                "is_active",
                "is_staff",
                "is_superuser",
                "groups",
                "user_permissions",
            )
        }),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )

    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": (
                "username",
                "email",
                "password1",
                "password2",
                "first_name",
                "last_name",
                "phone",
                "primary_org",
                "is_generated",   # ✅ added (optional)
            ),
        }),
    )


@admin.register(AdminAccess)
class AdminAccessAdmin(admin.ModelAdmin):
    autocomplete_fields = ['user', 'selected_organization']
    list_display = ['id', 'user', 'selected_organization', 'active', 'super_user']
    list_filter = ['user', 'selected_organization', 'active', 'super_user']

@admin.register(EmailChangeRequest)
class EmailChangeRequestAdmin(admin.ModelAdmin):
    autocomplete_fields = ['user']
    list_display = ['id', 'user', 'new_email', 'code', 'created_at', 'expires_at', 'used']
    list_filter = ['user', 'created_at', 'expires_at', 'used']
    search_fields = ['new_email', 'code']

@admin.register(EmailOTP)
class EmailOTPAdmin(admin.ModelAdmin):
    autocomplete_fields = ['user']
    list_display = ['id', 'user', 'code', 'created_at', 'expires_at', 'used']
    list_filter = ['user', 'created_at', 'expires_at', 'used']
    search_fields = ['code']
