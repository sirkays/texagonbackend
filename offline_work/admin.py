from django.contrib import admin
from .models import OfflinePracticalWork, OfflinePracticalScore


@admin.register(OfflinePracticalWork)
class OfflinePracticalWorkAdmin(admin.ModelAdmin):
    list_display = [
        "title",
        "assessment_type",
        "course",
        "max_score",
        "conducted_at",
        "visibility",
        "created_by",
        "created_at",
    ]
    list_filter = ["assessment_type", "visibility", "created_at"]
    search_fields = ["title", "course__title", "created_by__user__email"]
    ordering = ["-created_at"]
    raw_id_fields = ["course", "created_by", "academic_session"]


@admin.register(OfflinePracticalScore)
class OfflinePracticalScoreAdmin(admin.ModelAdmin):
    list_display = [
        "opw",
        "student",
        "score",
        "recorded_at",
        "recorded_by",
    ]
    list_filter = ["opw", "recorded_at"]
    search_fields = [
        "student__user__email",
        "student__user__first_name",
        "student__user__last_name",
        "opw__title",
    ]
    ordering = ["-recorded_at"]
    raw_id_fields = ["opw", "student", "recorded_by"]
