# achievements/admin.py
from django.contrib import admin
from gamification.models import (
    ActivityEvent,
    AchievementDefinition,
    AchievementAcquired,
    Badge,
    BadgeAward,
    PointTransaction,
    Streak,
    LeaderboardSeason
)
from django.db import transaction



@admin.register(AchievementDefinition)
class AchievementDefinitionAdmin(admin.ModelAdmin):
    list_display = ("code", "title", "organization", "is_active", "points", "category")
    list_filter = ("is_active", "category", "organization")
    search_fields = ("code", "title", "description")
    readonly_fields = ("created_at", "updated_at")

@admin.register(AchievementAcquired)
class AchievementAcquiredAdmin(admin.ModelAdmin):
    list_display = ("student", "definition", "acquired_at", "value_at_unlock")
    list_filter = ("definition__organization", "definition__code")
    search_fields = ("student__user__email", "definition__code", "definition__title")

@admin.register(Badge)
class BadgeAdmin(admin.ModelAdmin):
    list_display = ("name", "organization", "points", "icon_name", "color")
    list_filter = ("organization",)
    search_fields = ("name", "criteria")

@admin.register(BadgeAward)
class BadgeAwardAdmin(admin.ModelAdmin):
    list_display = ("student", "badge", "awarded_at")
    list_filter = ("badge__organization",)
    search_fields = ("student__user__email", "badge__name")

@admin.register(PointTransaction)
class PointTransactionAdmin(admin.ModelAdmin):
    list_display = ("student", "points", "reason", "balance_after", "created_at")
    search_fields = ("student__user__email", "reason")
    list_filter = ("created_at",)

@admin.register(ActivityEvent)
class ActivityEventAdmin(admin.ModelAdmin):
    list_display = ("organization", "student", "event_type", "value", "occurred_at")
    list_filter = ("organization", "event_type")
    search_fields = ("student__user__email", "event_type")
    date_hierarchy = "occurred_at"

@admin.register(Streak)
class StreakAdmin(admin.ModelAdmin):
    list_display = ("student", "current_days", "longest_days", "last_activity")





@admin.register(LeaderboardSeason)
class LeaderboardSeasonAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "organization",
        "start_at",
        "end_at",
        "is_active",
        "created_at",
    )
    list_filter = (
        "organization",
        "is_active",
        "start_at",
        "end_at",
    )
    search_fields = (
        "name",
        "slug",
        "organization__name",
    )
    ordering = ("-start_at",)
    date_hierarchy = "start_at"

    prepopulated_fields = {
        "slug": ("name",),
    }

    fieldsets = (
        (None, {
            "fields": (
                "organization",
                "name",
                "slug",
                "is_active",
            )
        }),
        ("Season Dates", {
            "fields": (
                "start_at",
                "end_at",
            )
        }),
        ("Metadata", {
            "fields": ("created_at",),
            "classes": ("collapse",),
        }),
    )

    readonly_fields = ("created_at",)

    @transaction.atomic
    def save_model(self, request, obj, form, change):
        """
        Enforce at most one active season per organization.
        If this season is marked active, deactivate others.
        """
        super().save_model(request, obj, form, change)

        if obj.is_active:
            (
                LeaderboardSeason.objects
                .filter(organization=obj.organization, is_active=True)
                .exclude(pk=obj.pk)
                .update(is_active=False)
            )
