# achievements/services/engine.py
from django.db import transaction
from django.db.models import Sum
from gamification.models import (
    AchievementAcquired,
    BadgeAward,
    PointTransaction,
    ActivityEvent,
)
from django.utils import timezone
from django.db import IntegrityError
from core.utils import resolve_season

def get_points_balance(student) -> int:
    # simplest: sum of transactions
    if getattr(student, "point_transactions", None) is None:
        return 0
    return int(student.point_transactions.aggregate(s=Sum("points"))["s"] or 0)


@transaction.atomic
def award_points(student, points: int, reason: str) -> None:
    if not points:
        return
    before = get_points_balance(student)
    after = before + int(points)
    leaderboard_season = resolve_season(student.organization, timezone.now())
    PointTransaction.objects.create(
        student=student,
        points=int(points),
        reason=reason,
        balance_after=after,
        season=leaderboard_season
    )


@transaction.atomic
def unlock_achievement(student, definition, value: int, meta: dict | None = None) -> bool:
    """
    Idempotent unlock. If already unlocked, returns False.
    """
    season = resolve_season(student.organization, timezone.now())
    obj, created = AchievementAcquired.objects.get_or_create(
        student=student,
        definition=definition,
        season=season,
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
    if student.organization:
        season = resolve_season(student.organization, timezone.now())

        obj, created = BadgeAward.objects.get_or_create(
            student=student,
            badge=badge,
            season=season,
            defaults={"reason": f"Reached {badge.points} points"},
        )
        return created
    return False



def log_event(*, student, org, event_type, value=1, meta=None, dedupe_key=None, occurred_at=None):
    if not org:
        return None  # skip safely

    meta = meta or {}
    occurred_at = occurred_at or timezone.now()

    # If you have a UniqueConstraint on (student, dedupe_key) or (org, student, dedupe_key),
    # then get_or_create is perfect:
    try:
        season = resolve_season(org, occurred_at)
        obj, created = ActivityEvent.objects.get_or_create(
            student=student,
            organization=org,
            dedupe_key=dedupe_key,   # must exist in DB schema
            season=season,
            defaults={
                "event_type": event_type,
                "value": value,
                "meta": meta,
                "occurred_at": occurred_at,
            },
        )
        if not created:
            return obj  # already logged
        return obj
    except IntegrityError:
        # Another worker/thread logged it first
        return ActivityEvent.objects.filter(
            student=student, organization=org, dedupe_key=dedupe_key
        ).first()
