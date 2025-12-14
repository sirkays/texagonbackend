# achievements/services/engine.py
from django.db import transaction
from django.db.models import Sum
from gamification.models import (
    AchievementAcquired,
    BadgeAward,
    PointTransaction,
    ActivityEvent,
)


def get_points_balance(student) -> int:
    # simplest: sum of transactions
    return int(student.point_transactions.aggregate(s=Sum("points"))["s"] or 0)


@transaction.atomic
def award_points(student, points: int, reason: str) -> None:
    if not points:
        return
    before = get_points_balance(student)
    after = before + int(points)
    PointTransaction.objects.create(
        student=student,
        points=int(points),
        reason=reason,
        balance_after=after,
    )


@transaction.atomic
def unlock_achievement(student, definition, value: int, meta: dict | None = None) -> bool:
    """
    Idempotent unlock. If already unlocked, returns False.
    """
    obj, created = AchievementAcquired.objects.get_or_create(
        student=student,
        definition=definition,
        defaults={
            "value_at_unlock": int(value),
            "meta": meta or {},
        },
    )
    if not created:
        return False

    # optional: award points for unlocking
    if definition.points:
        award_points(student, definition.points, f"Achievement: {definition.title}")

    return True


@transaction.atomic
def unlock_badge_if_eligible(student, badge) -> bool:
    """
    Points-threshold badges.
    """
    balance = get_points_balance(student)
    if balance < int(badge.points):
        return False

    obj, created = BadgeAward.objects.get_or_create(
        student=student,
        badge=badge,
        defaults={"reason": f"Reached {badge.points} points"},
    )
    return created


def log_event(*, org, student, event_type: str, value: int = 1, meta: dict | None = None):
    """
    Use this helper throughout your app whenever something happens.
    """
    return ActivityEvent.objects.create(
        organization=org,
        student=student,
        event_type=event_type,
        value=int(value),
        meta=meta or {},
    )
