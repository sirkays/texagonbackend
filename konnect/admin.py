from django.contrib import admin
from .models import KonnectRoom, KonnectRoomUser
from accounts.models import User
from learning.models import Course


class KonnectRoomUserInline(admin.TabularInline):
    model = KonnectRoomUser
    extra = 0
    autocomplete_fields = ["user"]
    readonly_fields = ["active", "created_at", "updated_at"]
    show_change_link = True

@admin.register(KonnectRoom)
class KonnectRoomAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "creator",
        "status",
        "room_id",
        "room_url",
        "courses_count",
        "users_count",
        "created_at",
    )

    list_filter = (
        "status",
        "created_at",
    )

    search_fields = (
        "name",
        "room_id",
        "creator__email",
        "creator__first_name",
        "creator__last_name",
    )

    readonly_fields = (
        "room_id",
        "created_at",
        "updated_at",
    )

    autocomplete_fields = [
        "creator",
        "allowed_courses",
        "allowed_users",
    ]

    inlines = [KonnectRoomUserInline]

    fieldsets = (
        ("Room Info", {
            "fields": ("name", "creator", "status", "room_id", "room_url")
        }),
        ("Messages", {
            "fields": ("welcome_message", "message")
        }),
        ("Permissions", {
            "fields": ("allowed_courses", "allowed_users")
        }),
        ("Timestamps", {
            "fields": ("created_at", "updated_at"),
            "classes": ("collapse",)
        }),
    )

    def courses_count(self, obj):
        return obj.allowed_courses.count()
    courses_count.short_description = "Courses"

    def users_count(self, obj):
        return obj.allowed_users.count()
    users_count.short_description = "Users"

@admin.register(KonnectRoomUser)
class KonnectRoomUserAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "user",
        "konnect_room",
        "active",
        "created_at",
    )

    list_filter = (
        "active",
        "konnect_room__status",
        "created_at",
    )

    search_fields = (
        "name",
        "user__email",
        "user__first_name",
        "user__last_name",
        "konnect_room__name",
        "konnect_room__room_id",
    )

    autocomplete_fields = [
        "user",
        "konnect_room",
    ]

    readonly_fields = (
        "created_at",
        "updated_at",
    )