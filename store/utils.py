from decimal import Decimal
from django.utils import timezone
from .models import Coupon

def is_coupon_usable(coupon: Coupon) -> bool:
    if not coupon or not coupon.active:
        return False

    now = timezone.now()

    if coupon.starts_at and now < coupon.starts_at:
        return False
    if coupon.ends_at and now > coupon.ends_at:
        return False

    if coupon.usage_limit is not None and coupon.used_count >= coupon.usage_limit:
        return False

    return True



def calc_discount(subtotal: Decimal, coupon: Coupon | None) -> Decimal:
    if not coupon or not is_coupon_usable(coupon):
        return Decimal("0.00")

    if subtotal <= Decimal("0.00"):
        return Decimal("0.00")

    if coupon.discount_type == Coupon.PERCENT:
        # If you intend value=10 => 10%, this is correct
        discount = (subtotal * (coupon.value / Decimal("100"))).quantize(Decimal("0.01"))
    else:  # FIXED
        discount = coupon.value.quantize(Decimal("0.01"))

    # never discount more than subtotal
    return min(discount, subtotal)



def _to_bool(v):
    return v in [True, "true", "True", 1, "1", "yes", "YES", "on", "ON"]


def _quant(v: Decimal) -> Decimal:
    return (v or Decimal("0.00")).quantize(Decimal("0.01"))


def _bnpl_customer_fees(principal_amount: Decimal, plan) -> Decimal:
    """
    customer_fee_rate is stored as decimal (e.g. 0.0500 for 5%)
    """
    principal_amount = Decimal(principal_amount or Decimal("0.00"))
    rate = Decimal(plan.customer_fee_rate or Decimal("0.0000"))
    flat = Decimal(plan.customer_fee_flat or Decimal("0.00"))
    return _quant(flat + (principal_amount * rate))