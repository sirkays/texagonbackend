from django.contrib import admin
from .models import AttendanceSession, AttendanceRecord

class AttendanceRecordInline(admin.TabularInline):
    model = AttendanceRecord
    extra = 0
    autocomplete_fields = ("student",)

@admin.register(AttendanceSession)
class AttendanceSessionAdmin(admin.ModelAdmin):
    list_display = ("course", "date", "topic", "created_at")
    list_filter = ("course", "date")
    search_fields = ("course__name", "topic")
    autocomplete_fields = ("course",)
    inlines = [AttendanceRecordInline]

@admin.register(AttendanceRecord)
class AttendanceRecordAdmin(admin.ModelAdmin):
    list_display = ("session", "student", "present", "created_at")
    list_filter = ("present", "session__course")
    search_fields = ("session__course__name", "student__user__username")
    autocomplete_fields = ("session", "student")
