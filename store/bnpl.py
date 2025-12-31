# store/api/bnpl.py
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from datetime import timedelta
from django.utils import timezone
from django.db.models import Q
from rest_framework import status
from .models import Product, BNPLPlanTemplate, Order
from texagonbackend.settings import TAX_RATE,FLAT_SHIPPING

TWOPLACES = Decimal("0.01")


def q2(amount: Decimal) -> Decimal:
    return amount.quantize(TWOPLACES, rounding=ROUND_HALF_UP)


def compute_bnpl_breakdown(
    *,
    principal_amount: Decimal,
    plan: BNPLPlanTemplate,
    currency: str = "NGN",
    first_due_at=None,
):
    product_price = q2(principal_amount)

    tax_amount = q2(product_price * TAX_RATE)
    shipping_amount = q2(FLAT_SHIPPING)

    principal_amount = q2(product_price + tax_amount + shipping_amount)

    fee_flat = q2(plan.customer_fee_flat or Decimal("0.00"))
    fee_rate = plan.customer_fee_rate or Decimal("0.0000")

    fee_rate_amount = q2(principal_amount * fee_rate)

    customer_fees = q2(fee_flat + fee_rate_amount)

    total_amount = q2(principal_amount + customer_fees)


    print("Total Amt: ", total_amount)

    print("Shipping: ",FLAT_SHIPPING, " customer fee: ", customer_fees)
    # Installments
    n = int(plan.num_installments)
    per_inst = q2(total_amount / Decimal(n))

    now = timezone.now()
    first_due = first_due_at or now

    installments = []
    running = Decimal("0.00")

    for i in range(1, n + 1):
        due_at = first_due if i == 1 else first_due + timedelta(days=plan.interval_days * (i - 1))
        amt = per_inst
        if i == n:
            amt = q2(total_amount - running)  # fix rounding on last
        installments.append(
            {
                "index": i,
                "due_at": due_at.isoformat(),
                "amount_due": str(amt),
                "capture_immediately": bool(i == 1 and plan.take_downpayment_now),
            }
        )
        running += amt

    downpayment_now = installments[0]["amount_due"] if plan.take_downpayment_now else "0.00"

    return {
        "principal_amount": str(principal_amount),
        "customer_fees": str(customer_fees),
        "total_amount": str(total_amount),
        "downpayment_now": downpayment_now,
        "currency": currency,
        "installments": installments,
    }


def pick_plan_for_product(product: Product, plan_id: str | None = None) -> BNPLPlanTemplate | None:
    if plan_id:
        return BNPLPlanTemplate.objects.filter(id=plan_id, active=True).first()

    if product.default_bnpl_plan_id:
        p = product.default_bnpl_plan
        if p and p.active:
            return p

    # fallback: any active plan (you can scope by provider/currency)
    return BNPLPlanTemplate.objects.filter(active=True).order_by("created_at").first()


def check_eligibility(principal_amount: Decimal, plan: BNPLPlanTemplate):
    if not plan.active:
        return False, "BNPL plan is not active."

    if principal_amount < (plan.min_amount or Decimal("0.00")):
        return False, f"Amount is below minimum for this BNPL plan ({plan.min_amount})."

    if plan.max_amount is not None and principal_amount > plan.max_amount:
        return False, f"Amount exceeds maximum for this BNPL plan ({plan.max_amount})."

    return True, ""


