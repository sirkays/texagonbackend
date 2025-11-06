from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from django.utils import timezone
from .models import SessionToken

class SessionTokenAuthentication(BaseAuthentication):
    keyword = "X-Session-Token"

    def authenticate(self, request):
        token = request.META.get("HTTP_X_SESSION_TOKEN")
        if not token:
            auth = request.META.get("HTTP_AUTHORIZATION", "")
            # allow "X-Session-Token <token>" *or* "Bearer <token>"
            if auth.startswith(f"{self.keyword} "):
                token = auth[len(self.keyword) + 1:].strip()
            elif auth.startswith("Bearer "):
                token = auth[len("Bearer ") :].strip()
        if not token:
            raise AuthenticationFailed("Session token required.")

        if token in (None, ""):
            raise AuthenticationFailed("Session token required.")
        try:
            st = SessionToken.objects.select_related("user").get(key=token, is_active=True)
        except SessionToken.DoesNotExist:
            raise AuthenticationFailed("Invalid or revoked session token.")

        if st.expires_at <= timezone.now():
            raise AuthenticationFailed("Session token expired.")

        return (st.user, None)
