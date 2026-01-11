from django.utils import timezone
from api.models import SessionToken

def revoke_all_user_sessions(user, reason: str = "subscription_expired"):
    """
    Immediately revoke all active session tokens for a user.
    """
    now = timezone.now()
    SessionToken.objects.filter(
        user=user,
        is_active=True,
    ).update(
        is_active=False,
        expires_at=now,
        meta={
            "revoked_reason": reason,
            "revoked_at": now.isoformat(),
        },
    )
