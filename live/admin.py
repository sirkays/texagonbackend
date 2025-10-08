from django.contrib import admin
from .models import LiveSession, TutoringBooking, PrivateTutoring, AvailableDay

# --------------------
# LiveSession
# --------------------
@admin.register(LiveSession)
class LiveSessionAdmin(admin.ModelAdmin):
    list_display = ("title", "course", "host", "scheduled_at", "duration_minutes", "created_at")
    list_filter = ("course", "host")
    search_fields = ("title", "course__name", "host__user__username")
    autocomplete_fields = ("course", "host")
    list_select_related = ("course", "host__user")
    ordering = ("-created_at",)


# --------------------
# TutoringBooking
# --------------------
@admin.register(TutoringBooking)
class TutoringBookingAdmin(admin.ModelAdmin):
    list_display = (
        "teacher",
        "student",
        "private_tutoring",
        "duration_hours",
        "price",
        "status",
        "created_at",
    )
    list_filter = (
        "status",
        "private_tutoring__course",
        "private_tutoring__teacher",
    )
    search_fields = (
        "teacher__user__username",
        "student__user__username",
        "private_tutoring__course__name",  # aligned to course.name
        "private_tutoring__notes",
    )
    autocomplete_fields = ("teacher", "student", "private_tutoring")
    readonly_fields = ("created_at", "updated_at")
    list_select_related = (
        "teacher__user",
        "student__user",
        "private_tutoring__course",
        "private_tutoring__teacher__user",
    )
    ordering = ("-created_at",)

    fieldsets = (
        ("Tutoring Booking Details", {
            "fields": (
                "teacher",
                "student",
                "private_tutoring",
                "duration_hours",
                "price",
                "status",
                "notes",
            )
        }),
        ("Timestamps", {
            "fields": ("created_at", "updated_at"),
            "classes": ("collapse",),
        }),
    )


# --------------------
# AvailableDay
# --------------------
@admin.register(AvailableDay)
class AvailableDayAdmin(admin.ModelAdmin):
    list_display = ("day", "private_tutoring")
    list_filter = ("day", "private_tutoring__teacher", "private_tutoring__course")
    search_fields = (
        "day",
        "private_tutoring__teacher__user__first_name",
        "private_tutoring__teacher__user__last_name",
        "private_tutoring__course__name",  # aligned to course.name
    )
    ordering = ("day",)
    list_select_related = ("private_tutoring__teacher__user", "private_tutoring__course")
    fieldsets = (
        ("Available Day Information", {
            "fields": ("day", "private_tutoring"),
        }),
    )


# --------------------
# PrivateTutoring (+ inline for AvailableDay)
# --------------------
class AvailableDayInline(admin.TabularInline):
    model = AvailableDay
    extra = 1

@admin.register(PrivateTutoring)
class PrivateTutoringAdmin(admin.ModelAdmin):
    list_display = (
        "teacher",
        "course",
        "rate_per_hour",
        "tutoring_duration_days",
        "created_at",
        "updated_at",
    )
    list_filter = ("teacher", "course", "tutoring_duration_days", "created_at")
    search_fields = (
        "teacher__user__first_name",
        "teacher__user__last_name",
        "course__name",  # aligned to course.name
        "notes",
    )
    readonly_fields = ("created_at", "updated_at")
    autocomplete_fields = ("teacher", "course")
    list_select_related = ("teacher__user", "course")
    ordering = ("-created_at",)
    inlines = [AvailableDayInline]

    fieldsets = (
        ("Tutoring Details", {
            "fields": (
                "teacher",
                "course",
                "rate_per_hour",
                "tutoring_duration_days",
                "notes",
            )
        }),
        ("Timestamps", {
            "fields": ("created_at", "updated_at"),
            "classes": ("collapse",),
        }),
    )
