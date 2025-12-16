from datetime import timedelta
from django.db.models.functions import TruncDate
from django.utils import timezone

def build_streak(base_qs):
    tz = timezone.get_current_timezone()  # Africa/Lagos in your project if configured

    # 1) What days exist in this queryset (as the DB sees them)?
    days_qs = (
        base_qs.annotate(day=TruncDate("created_at", tzinfo=tz))
               .values_list("day", flat=True)
               .distinct()
               .order_by("-day")
    )
    days = list(days_qs)

    # DEBUG: confirm the queryset contains both days
    if not days:
        return base_qs.none()

    anchor = days[0]
    wanted = {anchor}

    cur = anchor
    day_set = set(days)
    while (cur - timedelta(days=1)) in day_set:
        cur = cur - timedelta(days=1)
        wanted.add(cur)
        
    # 2) Return all objects whose local-day is in the streak
    return (
        base_qs.annotate(day=TruncDate("created_at", tzinfo=tz))
               .filter(day__in=wanted)
    )
