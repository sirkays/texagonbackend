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
)

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
