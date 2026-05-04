from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.db.models import Q, Count
from .models import AdminAccess, User, EmailOTP
from django import forms
from orgs.models import Organization


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


admin.site.register(AdminAccess)
admin.site.register(EmailOTP)