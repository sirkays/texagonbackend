from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Dict, Optional

from django.db.models import Q
from django.utils import timezone

from billing.models import UserAccountSubscription
from academics.models import ParentProfile, ParentChildLink, StudentProfile


@dataclass
class ChildSubscriptionRow:
    student_id: int
    student_user_id: int
    subscription_id: Optional[int]
    subscription_status: str  # active/expired/past_due/cancelled etc or "missing"
    end_at: Optional[str]
    is_subscribed_now: bool
    needs_renewal: bool


def _is_subscribed_now_obj(sub: UserAccountSubscription | None, now) -> bool:
    """
    True only if ACTIVE and end_at not passed (or end_at is None).
    """
    if not sub:
        return False
    if sub.status != UserAccountSubscription.Status.ACTIVE:
        return False
    if sub.end_at and now >= sub.end_at:
        return False
    return True


def get_parent_children_latest_subscriptions(
    *,
    parent: ParentProfile,
    enforce_billed_to_parent: bool = False,
) -> List[ChildSubscriptionRow]:
    """
    Returns one row PER CHILD with:
      - latest UserAccountSubscription (if any)
      - is_subscribed_now
      - needs_renewal (not subscribed now)

    This is DB-efficient:
      - gets all child user_ids in one query
      - gets latest subscription per user using DISTINCT ON (Postgres)
    """
    now = timezone.now()

    # 1) get children (student_id + user_id) in one query
    children = list(
        ParentChildLink.objects.filter(parent=parent)
        .select_related("student__user")
        .values("student_id", "student__user_id")
    )

    if not children:
        return []

    user_ids = [c["student__user_id"] for c in children]

    # 2) latest subscription per user in this org
    subs_qs = UserAccountSubscription.objects.filter(
        organization=parent.organization,
        user_id__in=user_ids,
    )

    # optional: only subscriptions billed to this parent
    if enforce_billed_to_parent:
        subs_qs = subs_qs.filter(billed_to_parent=parent)

    # ✅ Postgres-only fast pattern: latest row per user
    # order_by(user_id, -start_at, -id) + distinct(user_id)
    latest_subs = list(
        subs_qs.order_by("user_id", "-start_at", "-id").distinct("user_id")
    )

    latest_by_user: Dict[int, UserAccountSubscription] = {s.user_id: s for s in latest_subs}

    # 3) build results per child
    rows: List[ChildSubscriptionRow] = []
    for c in children:
        student_id = int(c["student_id"])
        uid = int(c["student__user_id"])
        sub = latest_by_user.get(uid)

        is_ok = _is_subscribed_now_obj(sub, now)
        rows.append(
            ChildSubscriptionRow(
                student_id=student_id,
                student_user_id=uid,
                subscription_id=sub.id if sub else None,
                subscription_status=sub.status if sub else "missing",
                end_at=sub.end_at.isoformat() if (sub and sub.end_at) else None,
                is_subscribed_now=is_ok,
                needs_renewal=not is_ok,
            )
        )

    return rows

def student_subscription_status(student) -> dict:
    now = timezone.now()

    sub = (
        UserAccountSubscription.objects
        .filter(organization=student.organization, user=student.user)
        .order_by("-start_at", "-id")
        .only("id", "status", "end_at")
        .first()
    )
    
    if sub.status == UserAccountSubscription.Status.ACTIVE:
        return {"ok": True, "reason": "active", "subscription_id": sub.id}

    if student.get_course_allowed(is_general_activation=True).exists():
        return {"ok": True, "reason": "active", "subscription_id": sub.id} 

    if not sub:
        return {"ok": False, "reason": "missing", "subscription_id": None, "message":"Subscription not found."}

    if sub.status == UserAccountSubscription.Status.PAST_DUE:
        return {"ok": False, "reason": "past_due", "subscription_id": sub.id, "message":"Subscription has expired."}

    if sub.status in {UserAccountSubscription.Status.CANCELLED, UserAccountSubscription.Status.EXPIRED}:
        return {"ok": False, "reason": sub.status, "subscription_id": sub.id, "message":"Subscription cancelled or expired."}

    if sub.status == UserAccountSubscription.Status.ACTIVE and sub.end_at and now >= sub.end_at:
        return {"ok": False, "reason": "expired_by_date", "subscription_id": sub.id, "message":"Subscription has expired."}

    return {"ok": False, "reason": "not_active", "subscription_id": sub.id, "message":"Subscription not active."}



def parent_needs_to_subscribe_again(
    *,
    parent: ParentProfile,
    enforce_billed_to_parent: bool = False,
) -> bool:
    """
    True if ANY child is not subscribed now (missing/expired/past_due/cancelled).
    """
    rows = get_parent_children_latest_subscriptions(
        parent=parent,
        enforce_billed_to_parent=enforce_billed_to_parent,
    )
    return any(r.needs_renewal for r in rows)
