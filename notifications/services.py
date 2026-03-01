# texagon_academy\texagonbackend\notifications\services.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional, Sequence

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.utils import timezone

from .models import Notification
from .messages import MessageSpec


@dataclass
class DispatchResult:
    created_notifications: int
    emails_sent: int


def dispatch(
    *,
    users: Sequence[Any],
    message: MessageSpec,
    ctx: Optional[Dict[str, Any]] = None,
    data: Optional[Dict[str, Any]] = None,
    send_in_app: bool = True,
    send_email: bool = False,
    email_from: Optional[str] = None,
    fail_silently: bool = True,
) -> DispatchResult:
    """
    Universal notification/email dispatcher.

    - Creates Notification records (bulk)
    - Optionally sends emails
    - message templates can use context variables like {{ user }}, {{ data }}, etc.
    """
    ctx = ctx or {}
    data = data or {}

    # allow `data` to override message.default_data
    merged_data = {**(message.default_data or {}), **(data or {})}

    notifications_to_create: list[Notification] = []
    emails_sent = 0

    # Use a DB transaction for notification creation
    with transaction.atomic():
        if send_in_app:
            for user in users:
                per_user_ctx = {
                    **ctx,
                    "user": user,
                    "data": merged_data,
                    "now": timezone.now(),
                    "logo_url":"https://res.cloudinary.com/dqzqyeijp/image/upload/v1769618417/techxagon_logo_xbv3k2.png",
                }

                title = message.render_title(per_user_ctx)
                body = message.render_body(per_user_ctx)

                notifications_to_create.append(
                    Notification(
                        user=user,
                        kind=message.kind,
                        title=title,
                        body=body,
                        data=merged_data,
                    )
                )

            if notifications_to_create:
                Notification.objects.bulk_create(notifications_to_create)

    if send_email:
        email_from = email_from or getattr(settings, "DEFAULT_FROM_EMAIL", None)

        for user in users:
            user_email = getattr(user, "email", None)
            if not user_email:
                continue

            per_user_ctx = {
                **ctx,
                "user": user,
                "data": merged_data,
                "now": timezone.now(),
            }

            subject = message.render_email_subject(per_user_ctx) or message.render_title(per_user_ctx)
            text_body, html_body = message.render_email(per_user_ctx)

            if not text_body and not html_body:
                text_body = message.render_body(per_user_ctx)

            if "testtechxagonacademy.com" in user_email:
                return 
                
            # ✅ staging: send inline, production: queue
            if getattr(settings, "USE_CELERY", False):
                from .tasks import send_email_task
                send_email_task.delay(
                    to=[user_email],
                    subject=subject,
                    text_body=text_body,
                    html_body=html_body,
                    from_email=email_from,
                )
                emails_sent += 1
            else:
                try:
                    _send_email(
                        to=[user_email],
                        subject=subject,
                        text_body=text_body,
                        html_body=html_body,
                        from_email=email_from,
                    )
                    emails_sent += 1
                except Exception:
                    if not fail_silently:
                        raise

    return DispatchResult(
        created_notifications=len(notifications_to_create) if send_in_app else 0,
        emails_sent=emails_sent,
    )


def mark_as_read(notification: Notification) -> Notification:
    if not notification.is_read:
        notification.is_read = True
        notification.read_at = timezone.now()
        notification.save(update_fields=["is_read", "read_at"])
    return notification


def _send_email(*, to: list[str], subject: str, text_body: Optional[str], html_body: Optional[str], from_email: str):
    msg = EmailMultiAlternatives(
        subject=subject,
        body=text_body or "",
        from_email=from_email,
        to=to,
    )
    if html_body:
        msg.attach_alternative(html_body, "text/html")
    msg.send(fail_silently=False)
