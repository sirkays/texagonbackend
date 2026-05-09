from django.contrib import admin
from .models import StudentDevice, Tier


@admin.register(StudentDevice)
class StudentDeviceAdmin(admin.ModelAdmin):
    autocomplete_fields = ['student']
    list_display = (
        "student",
        "device_id_short",
        "user_agent_short",
        "first_seen",
        "last_seen",
    )
    list_filter = ("first_seen", "last_seen")
    search_fields = (
        "student__user__username",
        "student__user__email",
        "device_id",
        "user_agent",
    )
    readonly_fields = ("first_seen", "last_seen")
    ordering = ("-last_seen",)

    fieldsets = (
        (None, {
            "fields": (
                "student",
                "device_id",
                "user_agent",
                "ip_hash",
            )
        }),
        ("Timestamps", {
            "fields": (
                "first_seen",
                "last_seen",
            )
        }),
    )

    def device_id_short(self, obj):
        """Show first 8 chars of device id for readability."""
        return obj.device_id[:8]
    device_id_short.short_description = "Device ID"

    def user_agent_short(self, obj):
        """Show a truncated version of the user agent."""
        return (obj.user_agent[:60] + "…") if len(obj.user_agent) > 60 else obj.user_agent
    user_agent_short.short_description = "User Agent"
