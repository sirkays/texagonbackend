from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import List, Dict, Tuple, Set

from django.db import transaction
from django.utils import timezone

from orgs.models import OrganizationMembership
from billing.models import SubscriptionInvoice, InvoiceType, OrganizationSubscription
from academics.models import ParentProfile, ParentChildLink


@dataclass
class GenerateResult:
    created: int
    skipped_existing: int
    parents_processed: int


def _billing_days(subscription: OrganizationSubscription) -> int:
    plan = getattr(subscription, "plan", None)
    if not plan:
        return 30
    try:
        return int(plan.billing_period)
    except Exception:
        return 30


def _period_bounds(now, days: int):
    """
    Define current billing cycle as [period_start, period_end)
    For monthly-like billing (30 days) this is okay.
    If you later want "calendar month", tell me and I’ll switch.
    """
    period_end = now
    period_start = now - timedelta(days=days)
    return period_start, period_end


def generate_parent_children_subscription_invoices(
    *,
    org_id: int | None = None,
    now=None,
    due_in_days: int = 3,
    dry_run: bool = False,
    batch_size: int = 1000,
) -> GenerateResult:
    """
    Generates subscription invoices for each parent's children based on ParentProfile.organization_subscription.
    One invoice per child per billing cycle (plan.billing_period days).

    Idempotent: skips if an invoice already exists for (parent_profile, student_id, period_start, plan_id).
    """
    now = now or timezone.now()

    parents_qs = ParentProfile.objects.select_related(
        "organization", "organization_subscription__plan", "user"
    ).filter(
        organization_subscription__isnull=False,
        organization_subscription__status=OrganizationSubscription.Status.ACTIVE,
    )

    if org_id:
        parents_qs = parents_qs.filter(organization_id=org_id)

    parents = list(parents_qs)
    if not parents:
        return GenerateResult(created=0, skipped_existing=0, parents_processed=0)

    # Build all candidate (parent_profile_id, student_id) pairs in one go
    parent_ids = [p.id for p in parents]
    links = ParentChildLink.objects.filter(parent_id__in=parent_ids).select_related("student", "parent")

    # Map parent_id -> subscription/plan/billing_days/membership
    parent_meta: Dict[int, dict] = {}
    print(parents, " all parents...")
    for p in parents:
        sub = p.organization_subscription
        plan = getattr(sub, "plan", None)
        if not sub or not plan:
            continue

        days = _billing_days(sub)
        period_start, period_end = _period_bounds(now, days)

        membership, _ = OrganizationMembership.objects.get_or_create(
            user=p.user,
            organization=p.organization,
            role=OrganizationMembership.Role.PARENT,
            defaults={"is_active": True},
        )
        
        print(p.user, " user....")
        parent_meta[p.id] = {
            "parent": p,
            "subscription": sub,
            "plan": plan,
            "days": days,
            "period_start": period_start,
            "period_end": period_end,
            "membership_id": membership.id,
            "currency": getattr(sub, "currency", None) or "NGN",
        }

    candidates: List[Tuple[int, int]] = []  # (parent_id, student_id)
    for l in links:
        if l.parent_id in parent_meta and l.student_id:
            candidates.append((l.parent_id, l.student_id))

    if not candidates:
        return GenerateResult(created=0, skipped_existing=0, parents_processed=len(parents))

    # Build a set of keys that already exist to avoid duplicates
    # We'll search existing invoices within a safe window (last 2 billing periods)
    # and match on meta keys.
    earliest_start = min(pm["period_start"] for pm in parent_meta.values())
    existing_qs = SubscriptionInvoice.objects.filter(
        issued_at__gte=earliest_start - timedelta(days=60)
    ).only("id", "meta", "organization_membership_id")

    existing_keys: Set[Tuple[int, int, str, int]] = set()
    # (parent_profile_id, student_id, period_start_iso, plan_id)
    for inv in existing_qs.iterator(chunk_size=2000):
        meta = inv.meta or {}
        if meta.get("invoice_kind") != "subscription":
            continue
        pid = meta.get("parent_profile_id")
        sid = meta.get("student_id")
        ps = meta.get("bill_period_start")
        plan_id = meta.get("plan_id")
        if pid and sid and ps and plan_id:
            existing_keys.add((int(pid), int(sid), str(ps), int(plan_id)))

    # Prepare new invoice rows
    invoice_rows: List[SubscriptionInvoice] = []
    invtype_rows: List[InvoiceType] = []

    skipped = 0

    for parent_id, student_id in candidates:
        pm = parent_meta.get(parent_id)
        if not pm:
            continue

        plan = pm["plan"]
        sub = pm["subscription"]
        membership_id = pm["membership_id"]
        currency = pm["currency"]
        period_start = pm["period_start"]
        period_end = pm["period_end"]

        key = (parent_id, student_id, period_start.isoformat(), plan.id)
        if key in existing_keys:
            skipped += 1
            continue

        issued_at = now
        due_at = now + timedelta(days=due_in_days)
        amount = Decimal(getattr(plan, "price", 0) or 0)

        inv = SubscriptionInvoice(
            organization_membership_id=membership_id,
            subscription=sub,
            amount=amount,
            currency=currency,
            issued_at=issued_at,
            due_at=due_at,
            status=SubscriptionInvoice.Status.OPEN,
            meta={
                "invoice_kind": "subscription",
                "parent_profile_id": parent_id,
                "student_id": student_id,
                "plan_id": plan.id,
                "bill_period_start": period_start.isoformat(),
                "bill_period_end": period_end.isoformat(),
            },
        )
        invoice_rows.append(inv)

    if dry_run:
        return GenerateResult(created=len(invoice_rows), skipped_existing=skipped, parents_processed=len(parents))

    with transaction.atomic():
        created_invoices = SubscriptionInvoice.objects.bulk_create(invoice_rows, batch_size=batch_size)

        # create InvoiceType rows (one per invoice / student)
        for inv in created_invoices:
            sid = str((inv.meta or {}).get("student_id"))
            invtype_rows.append(
                InvoiceType(
                    invoice=inv,
                    invoice_type=InvoiceType.Paytype.SUBSCRIPTION,
                    object_id=sid,
                    object_type="student",
                    meta={
                        "plan_id": (inv.meta or {}).get("plan_id"),
                        "bill_period_start": (inv.meta or {}).get("bill_period_start"),
                        "bill_period_end": (inv.meta or {}).get("bill_period_end"),
                    },
                )
            )

        InvoiceType.objects.bulk_create(invtype_rows, batch_size=batch_size)

        # Update last_billed_at per parent (optional but useful)
        # Set to now since we billed "this cycle"
        ParentProfile.objects.filter(id__in=[p.id for p in parents]).update(last_billed_at=now)

    return GenerateResult(created=len(created_invoices), skipped_existing=skipped, parents_processed=len(parents))
