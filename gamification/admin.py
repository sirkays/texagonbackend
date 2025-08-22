from django.contrib import admin
from .models import Badge, BadgeAward, PointTransaction, Streak

class BadgeAwardInline(admin.TabularInline):
    model = BadgeAward
    extra = 0
    autocomplete_fields = ("student",)

@admin.register(Badge)
class BadgeAdmin(admin.ModelAdmin):
    list_display = ("name", "organization", "created_at")
    list_filter = ("organization",)
    search_fields = ("name", "organization__name")
    autocomplete_fields = ("organization",)
    inlines = [BadgeAwardInline]

@admin.register(BadgeAward)
class BadgeAwardAdmin(admin.ModelAdmin):
    list_display = ("badge", "student", "awarded_at", "reason", "created_at")
    list_filter = ("badge",)
    search_fields = ("badge__name", "student__user__username")
    autocomplete_fields = ("badge", "student")

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
