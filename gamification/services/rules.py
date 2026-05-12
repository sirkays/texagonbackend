# achievements/services/rules.py
from datetime import timedelta
from django.db.models import Sum, Max
from django.utils import timezone
from gamification.models import ActivityEvent,Streak
from django.db.models.functions import TruncDate
from .streaks import build_streak

SUPPORTED_METRICS = {"count", "sum", "max", "distinct_count", "consecutive"}


def _apply_window(qs, window_days):
    if not window_days:
        return qs
    since = timezone.now() - timedelta(days=int(window_days))
    return qs.filter(occurred_at__gte=since)


def _apply_filters(qs, filters: dict):
    """
    filters maps meta keys to values, e.g. {"course_id": 3, "skill": "python"}
    stored in ActivityEvent.meta JSON.
    """
    if not filters:
        return qs
    for k, v in filters.items():
        qs = qs.filter(**{f"meta__{k}": v})
    return qs


def compute_rule_value(*, org_id: int, student_id: int, rule: dict) -> int:
    """
    Returns the current progress value for this rule.
    """
    metric = (rule or {}).get("metric")
    event_type = (rule or {}).get("event_type")


    if metric not in SUPPORTED_METRICS:
        return 0
    if not event_type:
        return 0

    qs = ActivityEvent.objects.filter(
        organization_id=org_id,
        student_id=student_id,
        event_type=event_type,
    )
    qs = _apply_window(qs, rule.get("window_days"))
    qs = _apply_filters(qs, rule.get("filters") or {})

    if metric == "count":
        return qs.count()

    if metric == "sum":
        return int(qs.aggregate(s=Sum("value"))["s"] or 0)

    if metric == "max":
        return int(qs.aggregate(m=Max("value"))["m"] or 0)

    if metric == "distinct_count":
        distinct_key = rule.get("distinct_key")
        if not distinct_key:
            return 0
        # count distinct values of meta[distinct_key]
        return qs.values(f"meta__{distinct_key}").distinct().count()
    
    if metric == "consecutive":
        day_count, _ = build_streak(qs)
        return day_count
    return 0


def get_target(rule: dict) -> int:
    try:
        return int((rule or {}).get("target") or 0)
    except Exception:
        return 0

