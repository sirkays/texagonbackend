from django.contrib import admin
from .models import LiveSession, TutoringBooking

@admin.register(LiveSession)
class LiveSessionAdmin(admin.ModelAdmin):
    list_display = ("title", "course", "host", "scheduled_at", "duration_minutes", "created_at")
    list_filter = ("course", "host")
    search_fields = ("title", "course__name", "host__user__username")
    autocomplete_fields = ("course", "host")

@admin.register(TutoringBooking)
class TutoringBookingAdmin(admin.ModelAdmin):
    list_display = ("organization", "teacher", "student",  "duration_hours", "price", "status", "created_at")
    list_filter = ("organization", "status")
    search_fields = ("teacher__user__username", "student__user__username", "organization__name")
    autocomplete_fields = ("organization", "teacher", "student")
