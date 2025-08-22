from django.conf import settings
from django.db import models
from django.utils import timezone
from datetime import timedelta
import secrets

class SessionToken(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="session_tokens")
    key = models.CharField(max_length=64, unique=True, db_index=True)
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    is_active = models.BooleanField(default=True)
    meta = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return f"{self.user_id}:{self.key[:6]}..."

    @classmethod
    def create_for_user(cls, user, hours_valid: int = 24, **meta):
        key = secrets.token_urlsafe(48)[:64]
        now = timezone.now()
        obj = cls.objects.create(
            user=user,
            key=key,
            created_at=now,
            expires_at=now + timedelta(hours=hours_valid),
            is_active=True,
            meta=meta or {},
        )
        return obj

    def revoke(self):
        self.is_active = False
        self.save(update_fields=["is_active"])
