from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.db.models import Q, Count
from .models import AdminAccess, User
from django import forms
from orgs.models import Organization


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    # Show extra fields
    list_display = ( "email", "first_name", "last_name", "phone", "primary_org", "is_staff", "is_active")
    list_filter = ("is_staff", "is_superuser", "is_active", "groups")
    search_fields = ( "email", "first_name", "last_name", "phone")
    ordering = ("email",)

    fieldsets = (
        (None, {"fields": ( "password",)}),
        ("Personal info", {"fields": ("first_name", "last_name", "email", "phone", "avatar", "primary_org")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )

    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ( "email", "password1", "password2", "first_name", "last_name", "phone", "primary_org"),
        }),
    )

admin.site.register(AdminAccess)

