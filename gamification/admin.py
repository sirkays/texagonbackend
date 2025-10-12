# achievements/admin.py
from django.contrib import admin
from django.db.models import Prefetch

from .models import Badge, BadgeAward, AchievementDefinition, PointTransaction, Streak


@admin.register(AchievementDefinition)
class AchievementDefinitionAdmin(admin.ModelAdmin):
    list_display = ("code", "title", "organization", "target_value", "points", "is_active")
    list_filter = ("organization", "is_active")
    search_fields = ("code", "title", "description", "category")
    ordering = ("code",)
    autocomplete_fields = ("organization",)
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related("organization")


@admin.register(Badge)
class BadgeAdmin(admin.ModelAdmin):
    list_display = ("name", "organization", "points", "icon_name", "color")
    list_filter = ("organization",)
    search_fields = ("name", "criteria")
    autocomplete_fields = ("organization",)
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related("organization")


class BadgeOrganizationFilter(admin.SimpleListFilter):
    """Filter BadgeAwards by Organization via related Badge without using __ lookups in list_filter."""
    title = "organization"
    parameter_name = "organization"

    def lookups(self, request, model_admin):
        from orgs.models import Organization
        return [(o.id, str(o)) for o in Organization.objects.order_by("name")]

    def queryset(self, request, queryset):
        org_id = self.value()
        if org_id:
            return queryset.filter(badge__organization_id=org_id)
        return queryset


@admin.register(BadgeAward)
class BadgeAwardAdmin(admin.ModelAdmin):
    list_display = ("badge", "student", "awarded_at", "reason")
    list_filter = (BadgeOrganizationFilter,)
    search_fields = ("student__user__email", "badge__name")
    autocomplete_fields = ("badge", "student")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related("badge", "student")


@admin.register(PointTransaction)
class PointTransactionAdmin(admin.ModelAdmin):
    list_display = ("student", "points", "reason", "balance_after", "created_at")
    list_filter = ("student",)
    search_fields = ("student__user__username", "reason")
    autocomplete_fields = ("student",)
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related("student")


@admin.register(Streak)
class StreakAdmin(admin.ModelAdmin):
    list_display = ("student", "current_days", "longest_days", "last_activity", "created_at")
    list_filter = ("last_activity",)
    search_fields = ("student__user__username",)
    autocomplete_fields = ("student",)
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related("student")
