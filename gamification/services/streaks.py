# achievements/services/streaks.py
from datetime import timedelta
from django.db.models.functions import TruncDate
from django.utils import timezone

def build_streak(base_qs):
    """
    Compute the current consecutive-day streak from a queryset of ActivityEvents.

    Returns a tuple: (streak_day_count, streak_event_qs)
      - streak_day_count: int, the number of consecutive days ending at the most recent day
      - streak_event_qs: QuerySet, filtered to only events within the streak window
    """
    tz = timezone.get_current_timezone()

    # 1) What days exist in this queryset (as the DB sees them)?
    days_qs = (
        base_qs.annotate(day=TruncDate("created_at", tzinfo=tz))
               .values_list("day", flat=True)
               .distinct()
               .order_by("-day")
    )
    days = list(days_qs)

    if not days:
        return 0, base_qs.none()

    anchor = days[0]
    wanted = {anchor}

    cur = anchor
    day_set = set(days)
    while (cur - timedelta(days=1)) in day_set:
        cur = cur - timedelta(days=1)
        wanted.add(cur)

    # streak_day_count = number of unique consecutive days
    streak_day_count = len(wanted)

    # streak_event_qs = all events whose local-day is in the streak
    streak_event_qs = (
        base_qs.annotate(day=TruncDate("created_at", tzinfo=tz))
               .filter(day__in=wanted)
    )

    return streak_day_count, streak_event_qs
