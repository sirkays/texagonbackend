from django.contrib import admin
from django.utils.timezone import now
from .models import SessionToken

@admin.register(SessionToken)
class SessionTokenAdmin(admin.ModelAdmin):
    list_display = ("user", "key", "is_active", "expires_at", "created_at")
    list_filter = ("is_active", "expires_at", "created_at")
    search_fields = ("key", "user__username", "user__email")
    autocomplete_fields = ("user",)
    readonly_fields = ("key", "created_at")

    actions = ["revoke_tokens"]

    @admin.action(description="Revoke selected session tokens")
    def revoke_tokens(self, request, queryset):
        updated = queryset.update(is_active=False, expires_at=now())
        self.message_user(request, f"Revoked {updated} session token(s).")
