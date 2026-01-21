from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.response import Response

from api.authentication import SessionTokenAuthentication
from api.permissions import RequiresActiveStudentSubscription
from rest_framework_api_key.permissions import HasAPIKey
from .models import Notification


def _notification_to_dict(n: Notification):
    return {
        "id": n.id,
        "kind": n.kind,
        "title": n.title,
        "body": n.body,
        "is_read": n.is_read,
        "read_at": n.read_at.isoformat() if n.read_at else None,
        "data": n.data,
        "created_at": n.created_at.isoformat() if getattr(n, "created_at", None) else None,
    }


@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def my_notifications(request):
    """
    GET /api/notifications/?unread=true
    Returns all notifications for the current user (newest first).
    """
    unread = request.query_params.get("unread")
    qs = Notification.objects.filter(user=request.user).order_by("-created_at")

    if unread is not None and unread.lower() in {"1", "true", "yes"}:
        qs = qs.filter(is_read=False)

    notifications = [_notification_to_dict(n) for n in qs]
    return Response({"notifications": notifications}, status=status.HTTP_200_OK)


@api_view(["PATCH"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def update_notification_read_state(request, notification_id: int):
    """
    PATCH /api/notifications/<id>/read/
    Body: { "is_read": true } or { "is_read": false }
    - If is_read=true  => sets read_at to now (if not already set)
    - If is_read=false => clears read_at
    """
    try:
        n = Notification.objects.get(id=notification_id, user=request.user)
    except Notification.DoesNotExist:
        return Response({"detail": "Notification not found."}, status=status.HTTP_404_NOT_FOUND)

    if "is_read" not in request.data:
        return Response(
            {"detail": "Missing required field: is_read"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    is_read = bool(request.data["is_read"])

    n.is_read = is_read
    if is_read:
        # If you want to preserve original read time, only set if empty:
        if n.read_at is None:
            n.read_at = timezone.now()
    else:
        n.read_at = None

    n.save(update_fields=["is_read", "read_at"])
    return Response({"notification": _notification_to_dict(n)}, status=status.HTTP_200_OK)


@api_view(["PATCH"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def bulk_update_notifications_read_state(request):
    """
    PATCH /api/notifications/read-bulk/
    Body: { "ids": [1,2,3], "is_read": true }
    """
    ids = request.data.get("ids", [])
    if not isinstance(ids, list) or not ids:
        return Response({"detail": "ids must be a non-empty list."}, status=status.HTTP_400_BAD_REQUEST)

    if "is_read" not in request.data:
        return Response({"detail": "Missing required field: is_read"}, status=status.HTTP_400_BAD_REQUEST)

    is_read = bool(request.data["is_read"])
    now = timezone.now()

    qs = Notification.objects.filter(user=request.user, id__in=ids)

    if is_read:
        # mark read, set read_at (only where read_at is null if you prefer)
        qs.update(is_read=True, read_at=now)
    else:
        qs.update(is_read=False, read_at=None)

    return Response({"updated_ids": ids, "is_read": is_read}, status=status.HTTP_200_OK)
