from django.contrib import admin
from .models import Organization, OrganizationMembership, AcademicSession

class OrganizationMembershipInline(admin.TabularInline):
    model = OrganizationMembership
    extra = 0
    autocomplete_fields = ("user",)

class AcademicSessionInline(admin.TabularInline):
    model = AcademicSession
    extra = 0

@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "city", "state", "country", "contact_email", "is_active", "created_at")
    list_filter = ("is_active", "country", "state")
    search_fields = ("name", "slug", "city", "state", "country", "contact_email", "contact_phone")
    prepopulated_fields = {"slug": ("name",)}
    inlines = [OrganizationMembershipInline, AcademicSessionInline]

@admin.register(OrganizationMembership)
class OrganizationMembershipAdmin(admin.ModelAdmin):
    autocomplete_fields = ['user', 'organization']
    list_display = ("user", "organization", "role", "is_active", "created_at")
    list_filter = ("role", "is_active", "organization")
    search_fields = ("user__username", "user__email", "organization__name")

@admin.register(AcademicSession)
class AcademicSessionAdmin(admin.ModelAdmin):
    autocomplete_fields = ['organization']
    list_display = ("name", "organization", "start_date", "end_date", "is_current", "created_at")
    list_filter = ("organization", "is_current", "start_date", "end_date")
    search_fields = ("name", "organization__name")
