# billing/services/subscription_invoicing.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta, datetime, time
from decimal import Decimal
from typing import Dict, List, Set, Tuple, Optional

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from orgs.models import OrganizationMembership
from billing.models import (
    SubscriptionInvoice,
    InvoiceType,
    OrganizationSubscription,
    UserAccountSubscription,
    SubscriptionPlan,
)
from academics.models import ParentProfile, ParentChildLink, StudentProfile


@dataclass
class GenerateResult:
    created: int
    skipped_existing: int
    parents_processed: int
    children_processed: int


# -----------------------------
# Helpers
# -----------------------------

def _billing_days(subscription: OrganizationSubscription) -> int:
    """
    billing_period is stored as string (days). Default 30 if missing/invalid.
    """
    plan = getattr(subscription, "plan", None)
    if not plan:
        return 30
    try:
        return max(int(plan.billing_period), 1)
    except Exception:
        return 30


def _floor_to_midnight(dt):
    """
    Normalize to local midnight for deterministic cycle calculations.
    """
    dt = timezone.localtime(dt)
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def _anchor_for_parent_subscription(parent: ParentProfile, sub: OrganizationSubscription, now):
    """
    Stable anchor for cycle calculations:
      - if subscription.start_date exists (DateField): convert to aware datetime at midnight
      - else use parent.created_at
      - else fallback to now
    """
    if getattr(sub, "start_date", None):
        # start_date is DateField
        naive = datetime.combine(sub.start_date, time.min)
        return timezone.make_aware(naive, timezone.get_current_timezone())
    return getattr(parent, "created_at", None) or now


def _cycle_bounds(anchor_dt, now, days: int):
    """
    Deterministic cycle bounds:
      cycle_index = floor((now_midnight - anchor_midnight) / days)
      period = [anchor + idx*days, anchor + (idx+1)*days)
    """
    now0 = _floor_to_midnight(now)
    anchor0 = _floor_to_midnight(anchor_dt)

    if now0 < anchor0:
        # Guard: if now is before anchor, start at anchor (cycle 0)
        return anchor0, anchor0 + timedelta(days=days), 0

    delta_days = (now0.date() - anchor0.date()).days
    cycle_index = delta_days // max(days, 1)

    period_start = anchor0 + timedelta(days=cycle_index * days)
    period_end = period_start + timedelta(days=days)
    return period_start, period_end, cycle_index


def _get_or_create_parent_membership(parent: ParentProfile) -> int:
    """
    Ensure parent has an OrganizationMembership(PARENT) for invoice linking.
    Returns membership_id.
    """
    membership, _ = OrganizationMembership.objects.get_or_create(
        user=parent.user,
        organization=parent.organization,
        role=OrganizationMembership.Role.PARENT,
        defaults={"is_active": True},
    )
    return membership.id


def _get_or_create_past_due_student_subscription(
    *,
    parent: ParentProfile,
    student: StudentProfile,
    plan: SubscriptionPlan,
    currency: str,
) -> UserAccountSubscription:
    """
    Ensure a UserAccountSubscription exists for this student (scoped to org) so invoices reference it.

    Rules:
      1) If ACTIVE exists -> return it (invoice references existing active sub).
      2) Else if a PAST_DUE exists for same parent+plan -> reuse it (update amount/currency).
      3) Else create new PAST_DUE (no access yet). Activation happens on payment.
    """
    # 1) Prefer ACTIVE (already subscribed)
    active = (
        UserAccountSubscription.objects
        .filter(
            organization=student.organization,
            user=student.user,
            status=UserAccountSubscription.Status.ACTIVE,
        )
        .order_by("-start_at")
        .first()
    )
    if active:
        return active

    # 2) Reuse existing PAST_DUE for same parent+plan to avoid duplicates
    past_due = (
        UserAccountSubscription.objects
        .filter(
            organization=student.organization,
            user=student.user,
            status=UserAccountSubscription.Status.PAST_DUE,
            plan=plan,
            billed_to_parent=parent,
        )
        .order_by("-start_at")
        .first()
    )
    amount = Decimal(getattr(plan, "price", 0) or 0)

    if past_due:
        # Keep amounts updated if plan price changed
        updates = {}
        if past_due.amount != amount:
            updates["amount"] = amount
        cur = currency or past_due.currency or "NGN"
        if past_due.currency != cur:
            updates["currency"] = cur
        if updates:
            for k, v in updates.items():
                setattr(past_due, k, v)
            past_due.save(update_fields=[*updates.keys(), "updated_at"])
        return past_due

    # 3) Create fresh PAST_DUE
    return UserAccountSubscription.objects.create(
        organization=student.organization,
        user=student.user,
        plan=plan,
        status=UserAccountSubscription.Status.PAST_DUE,
        start_at=timezone.now(),  # “created” timestamp; activation sets real window
        end_at=None,
        auto_renew=True,
        billed_to_parent=parent,
        amount=amount,
        currency=currency or "NGN",
        meta={
            "created_by": "invoice_generator",
            "parent_profile_id": parent.id,
            "student_id": student.id,
            "plan_id": plan.id,
        },
    )


# -----------------------------
# Main generator (complete)
# -----------------------------

def generate_parent_children_subscription_invoices(
    *,
    org_id: Optional[int] = None,
    now=None,
    due_in_days: int = 3,
    dry_run: bool = False,
    batch_size: int = 1000,
    user=None
) -> GenerateResult:
    """
    Creates subscription invoices for each parent's children, based on ParentProfile.organization_subscription.plan.

    Model behavior you asked for:
      - One invoice per (parent, child, billing cycle)
      - Attach `user_subscription` (never null) by creating/reusing a PAST_DUE UserAccountSubscription
      - Idempotent: no duplicates per billing cycle
      - Bulk create: safe for 10,000+/month

    Idempotency key:
      (parent_profile_id, student_id, cycle_index, plan_id)

    Where cycle_index is computed deterministically from (anchor, now, billing_period_days).

    Returns counts for monitoring.
    """
    now = now or timezone.now()

    # --- parents with active org subscription ---
    if user:
        parents_qs = (
            ParentProfile.objects
            .select_related("organization", "organization_subscription__plan", "user")
            .filter(
                user=user,
                organization_subscription__isnull=False,
                organization_subscription__status=OrganizationSubscription.Status.ACTIVE,
            )
        )
    else:
        parents_qs = (
            ParentProfile.objects
            .select_related("organization", "organization_subscription__plan", "user")
            .filter(
                organization_subscription__isnull=False,
                organization_subscription__status=OrganizationSubscription.Status.ACTIVE,
            )
        )
    if org_id:
        parents_qs = parents_qs.filter(organization_id=org_id)

    parents: List[ParentProfile] = list(parents_qs)
    if not parents:
        return GenerateResult(created=0, skipped_existing=0, parents_processed=0, children_processed=0)

    parent_ids = [p.id for p in parents]

    # --- fetch all parent-child links in one go ---
    links = (
        ParentChildLink.objects
        .filter(parent_id__in=parent_ids)
        .select_related("student__user", "student__organization", "parent")
    )

    # --- parent meta (subscription, plan, membership_id, cycle bounds) ---
    parent_meta: Dict[int, dict] = {}
    for p in parents:
        sub = p.organization_subscription
        plan = getattr(sub, "plan", None)
        if not sub or not plan:
            continue

        days = _billing_days(sub)
        anchor = _anchor_for_parent_subscription(p, sub, now)
        period_start, period_end, cycle_index = _cycle_bounds(anchor, now, days)

        membership_id = _get_or_create_parent_membership(p)

        parent_meta[p.id] = {
            "parent": p,
            "subscription": sub,
            "plan": plan,
            "days": days,
            "currency": getattr(sub, "currency", None) or "NGN",
            "membership_id": membership_id,
            "period_start": period_start,
            "period_end": period_end,
            "cycle_index": cycle_index,
        }

    # --- candidates: (parent_id, student_id) ---
    candidates: List[Tuple[int, int]] = []
    for l in links:
        if l.parent_id in parent_meta and l.student_id:
            candidates.append((l.parent_id, l.student_id))

    if not candidates:
        return GenerateResult(created=0, skipped_existing=0, parents_processed=len(parents), children_processed=0)

    # --- build existing keys (idempotency) ---
    # Narrow window: from earliest period_start across parents. Add some buffer.
    earliest_start = min(pm["period_start"] for pm in parent_meta.values())

    existing_qs = (
        SubscriptionInvoice.objects
        .filter(
            meta__invoice_kind="subscription",
            issued_at__gte=earliest_start - timedelta(days=120),
        )
        .only("id", "meta")
    )

    existing_keys: Set[Tuple[int, int, int, int]] = set()
    # (parent_profile_id, student_id, cycle_index, plan_id)
    for inv in existing_qs.iterator(chunk_size=2000):
        meta = inv.meta or {}
        pid = meta.get("parent_profile_id")
        sid = meta.get("student_id")
        cix = meta.get("cycle_index")
        plan_id = meta.get("plan_id")
        if pid and sid and (cix is not None) and plan_id:
            existing_keys.add((int(pid), int(sid), int(cix), int(plan_id)))

    # --- build invoice rows + invtype rows (invtype needs invoice PK, so do after bulk_create) ---
    invoice_rows: List[SubscriptionInvoice] = []

    skipped = 0
    billed_parent_ids: Set[int] = set()

    # We already have student objects in links (select_related), but we may see same student_id multiple times;
    # build a small cache by id for safety.
    student_cache: Dict[int, StudentProfile] = {}
    for l in links:
        if l.student_id and getattr(l, "student", None):
            student_cache[l.student_id] = l.student

    for parent_id, student_id in candidates:
        pm = parent_meta.get(parent_id)
        if not pm:
            continue

        plan = pm["plan"]
        sub = pm["subscription"]
        currency = pm["currency"]
        membership_id = pm["membership_id"]
        period_start = pm["period_start"]
        period_end = pm["period_end"]
        cycle_index = pm["cycle_index"]

        key = (parent_id, student_id, cycle_index, plan.id)
        if key in existing_keys:
            skipped += 1
            continue

        student = student_cache.get(student_id)
        if not student:
            # fallback lookup (should be rare)
            student = (
                StudentProfile.objects
                .select_related("user", "organization")
                .filter(id=student_id)
                .first()
            )
            if not student:
                continue
            student_cache[student_id] = student

        # optional safety: ensure org consistency between parent and student
        if student.organization_id != pm["parent"].organization_id:
            continue

        billed_parent_ids.add(parent_id)

        # Ensure user_subscription exists and is PAST_DUE (or ACTIVE if already subscribed)
        user_sub = _get_or_create_past_due_student_subscription(
            parent=pm["parent"],
            student=student,
            plan=plan,
            currency=currency,
        )

        inv = SubscriptionInvoice(
            organization_membership_id=membership_id,
            subscription=sub,                 # kept for reporting (org-level)
            user_subscription=user_sub,        # ✅ always set (not null)
            amount=Decimal(getattr(plan, "price", 0) or 0),
            currency=currency,
            issued_at=now,
            due_at=now + timedelta(days=due_in_days),
            status=SubscriptionInvoice.Status.OPEN,
            meta={
                "invoice_kind": "subscription",
                "parent_profile_id": parent_id,
                "student_id": student_id,
                "plan_id": plan.id,
                "cycle_index": cycle_index,  # ✅ idempotency anchor
                "bill_period_start": period_start.isoformat(),
                "bill_period_end": period_end.isoformat(),
            },
        )
        invoice_rows.append(inv)

    if dry_run:
        return GenerateResult(
            created=len(invoice_rows),
            skipped_existing=skipped,
            parents_processed=len(parents),
            children_processed=len(candidates),
        )

    if not invoice_rows:
        return GenerateResult(
            created=0,
            skipped_existing=skipped,
            parents_processed=len(parents),
            children_processed=len(candidates),
        )

    with transaction.atomic():
        created_invoices = SubscriptionInvoice.objects.bulk_create(invoice_rows, batch_size=batch_size)

        invtype_rows: List[InvoiceType] = []
        for inv in created_invoices:
            meta = inv.meta or {}
            student_id = meta.get("student_id")
            invtype_rows.append(
                InvoiceType(
                    invoice=inv,
                    invoice_type=InvoiceType.Paytype.SUBSCRIPTION,
                    object_id=str(student_id) if student_id else "",
                    object_type="student",
                    meta={
                        "plan_id": meta.get("plan_id"),
                        "cycle_index": meta.get("cycle_index"),
                        "bill_period_start": meta.get("bill_period_start"),
                        "bill_period_end": meta.get("bill_period_end"),
                    },
                )
            )
        InvoiceType.objects.bulk_create(invtype_rows, batch_size=batch_size)

        # Optional: track last_billed_at per parent (useful for UI)
        # Set to period_start for “this cycle”
        for pid in billed_parent_ids:
            pm = parent_meta.get(pid)
            if pm:
                ParentProfile.objects.filter(id=pid).update(last_billed_at=pm["period_start"])

    return GenerateResult(
        created=len(created_invoices),
        skipped_existing=skipped,
        parents_processed=len(parents),
        children_processed=len(candidates),
    )
