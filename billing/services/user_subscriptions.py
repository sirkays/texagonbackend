# billing/services/user_subscriptions.py

from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from billing.models import UserAccountSubscription, SubscriptionPlan
from academics.models import StudentProfile, ParentProfile


@transaction.atomic
def activate_user_subscription_from_paid_invoice(
    user_sub: UserAccountSubscription,
    *,
    plan: SubscriptionPlan,
    paid_at=None,
    currency="NGN",
):
    paid_at = paid_at or timezone.now()
    try:
        days = int(getattr(plan, "billing_period", 30))
    except Exception:
        days = 30

    # If already active and still valid, extend; else reset and activate
    if user_sub.status == UserAccountSubscription.Status.ACTIVE and user_sub.end_at and user_sub.end_at > paid_at:
        user_sub.end_at = user_sub.end_at + timedelta(days=days)
    else:
        user_sub.start_at = paid_at
        user_sub.end_at = paid_at + timedelta(days=days)

    user_sub.status = UserAccountSubscription.Status.ACTIVE
    user_sub.plan = plan
    user_sub.amount = Decimal(getattr(plan, "price", 0) or 0)
    user_sub.currency = currency or user_sub.currency or "NGN"
    user_sub.auto_renew = True
    user_sub.meta = {**(user_sub.meta or {}), "activated_at": paid_at.isoformat()}

    user_sub.save(update_fields=[
        "status", "plan", "start_at", "end_at",
        "amount", "currency", "auto_renew", "meta", "updated_at"
    ])
    return user_sub


def create_student_account_subscription(
    *,
    student: StudentProfile,
    parent: ParentProfile,
    plan: SubscriptionPlan | None = None,
    start_at=None,
    auto_renew: bool = True,
    currency: str = "NGN",
    force: bool = False,
) -> UserAccountSubscription:
    """
    Create (or reuse) a UserAccountSubscription for a STUDENT,
    billed to a PARENT.

    - One ACTIVE subscription per (organization, user)
    - Idempotent unless force=True
    """

    if student.organization_id != parent.organization_id:
        raise ValueError("Student and Parent must belong to the same organization")

    org = student.organization
    user = student.user

    # Determine plan
    if not plan:
        if not parent.organization_subscription:
            raise ValueError("Parent does not have an active organization subscription")
        plan = parent.organization_subscription.plan

    if not plan:
        raise ValueError("Subscription plan is required")

    now = timezone.now()
    start_at = start_at or now

    # Calculate end date from billing period (days)
    try:
        billing_days = int(plan.billing_period)
    except Exception:
        billing_days = 30

    end_at = start_at + timedelta(days=billing_days)

    amount = Decimal(plan.price or 0)

    with transaction.atomic():
        # Check for existing ACTIVE subscription
        existing = (
            UserAccountSubscription.objects
            .select_for_update()
            .filter(
                organization=org,
                user=user,
                status=UserAccountSubscription.Status.ACTIVE,
            )
            .first()
        )

        if existing and not force:
            return existing

        if existing and force:
            existing.status = UserAccountSubscription.Status.CANCELLED
            existing.end_at = now
            existing.save(update_fields=["status", "end_at", "updated_at"])

        sub = UserAccountSubscription.objects.create(
            organization=org,
            user=user,
            plan=plan,
            status=UserAccountSubscription.Status.ACTIVE,
            start_at=start_at,
            end_at=end_at,
            auto_renew=auto_renew,
            billed_to_parent=parent,
            amount=amount,
            currency=currency,
            meta={
                "subscription_kind": "student",
                "student_id": student.id,
                "parent_profile_id": parent.id,
                "plan_id": plan.id,
                "billing_days": billing_days,
            },
        )

    return sub
