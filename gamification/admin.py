from django.contrib import admin
from .models import Badge, BadgeAward, AchievementDefinition, PointTransaction, Streak


@admin.register(AchievementDefinition)
class AchievementDefinitionAdmin(admin.ModelAdmin):
    list_display = ("code", "title", "organization", "target_value", "points", "is_active")
    list_filter = ("organization", "is_active")
    search_fields = ("code", "title", "description", "category")
    ordering = ("code",)

@admin.register(Badge)
class BadgeAdmin(admin.ModelAdmin):
    list_display = ("name", "organization", "points", "icon_name", "color")
    list_filter = ("organization",)
    search_fields = ("name", "criteria")

@admin.register(BadgeAward)
class BadgeAwardAdmin(admin.ModelAdmin):
    list_display = ("badge", "student", "awarded_at")
    list_filter = ("badge__organization",)
    search_fields = ("student__user__email", "badge__name")

@admin.register(PointTransaction)
class PointTransactionAdmin(admin.ModelAdmin):
    list_display = ("student", "points", "reason", "balance_after", "created_at")
    list_filter = ("student",)
    search_fields = ("student__user__username", "reason")
    autocomplete_fields = ("student",)

@admin.register(Streak)
class StreakAdmin(admin.ModelAdmin):
    list_display = ("student", "current_days", "longest_days", "last_activity", "created_at")
    list_filter = ("last_activity",)
    search_fields = ("student__user__username",)
    autocomplete_fields = ("student",)
