from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from academics.models import StudentProfile, ParentProfile
from billing.models import UserAccountSubscription, SubscriptionPlan


@transaction.atomic
def activate_or_extend_student_subscription_from_invoice(
    *,
    student: StudentProfile,
    parent: ParentProfile | None,
    plan: SubscriptionPlan,
    paid_at=None,
    currency: str = "NGN",
) -> UserAccountSubscription:
    """
    Create or extend a student's UserAccountSubscription when a subscription invoice is PAID.

    Rules:
    - If an ACTIVE subscription exists and end_at is in the future -> extend from end_at
    - Else -> start now and end now + billing_period days
    - Always link billed_to_parent when provided
    - Idempotent at ACTIVE row level (unique constraint for active)
    """
    paid_at = paid_at or timezone.now()

    # billing period in days
    try:
        billing_days = int(plan.billing_period)
    except Exception:
        billing_days = 30

    # Lock any existing ACTIVE row for this org/user
    existing = (
        UserAccountSubscription.objects
        .select_for_update()
        .filter(
            organization=student.organization,
            user=student.user,
            status=UserAccountSubscription.Status.ACTIVE,
        )
        .order_by("-start_at")
        .first()
    )

    amount = Decimal(getattr(plan, "price", 0) or 0)

    if existing and existing.end_at and existing.end_at > paid_at:
        # Extend from current end
        new_end = existing.end_at + timedelta(days=billing_days)
        existing.plan = plan
        existing.amount = amount
        existing.currency = currency
        existing.auto_renew = True
        if parent:
            existing.billed_to_parent = parent
        existing.end_at = new_end
        existing.meta = {
            **(existing.meta or {}),
            "last_extended_at": paid_at.isoformat(),
            "billing_days": billing_days,
            "plan_id": plan.id,
        }
        existing.save(update_fields=[
            "plan", "amount", "currency", "auto_renew", "billed_to_parent",
            "end_at", "meta", "updated_at"
        ])
        return existing

    # If existing is active but expired or missing end_at, we can "reset" it
    if existing:
        existing.status = UserAccountSubscription.Status.EXPIRED
        existing.end_at = existing.end_at or paid_at
        existing.save(update_fields=["status", "end_at", "updated_at"])

    # Create new ACTIVE subscription
    start_at = paid_at
    end_at = paid_at + timedelta(days=billing_days)

    return UserAccountSubscription.objects.create(
        organization=student.organization,
        user=student.user,
        plan=plan,
        status=UserAccountSubscription.Status.ACTIVE,
        start_at=start_at,
        end_at=end_at,
        auto_renew=True,
        billed_to_parent=parent,
        amount=amount,
        currency=currency,
        meta={
            "subscription_kind": "student",
            "student_id": student.id,
            "parent_profile_id": getattr(parent, "id", None),
            "plan_id": plan.id,
            "billing_days": billing_days,
            "activated_at": paid_at.isoformat(),
        },
    )
