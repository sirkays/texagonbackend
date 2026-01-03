from decimal import Decimal
from django.utils import timezone
from django.db.models import Avg, Count
from texagonbackend.settings import TAX_RATE, FLAT_SHIPPING
from .models import Coupon, Product, Order, OrderItem, Review, Cart


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



def _quant(amount: Decimal) -> Decimal:
    return Decimal(amount).quantize(Decimal("0.01"))


def _bnpl_customer_fees(principal_amount: Decimal, plan) -> Decimal:
    """
    customer_fee_rate is stored as decimal (e.g. 0.0500 for 5%)
    """
    principal_amount = Decimal(principal_amount or Decimal("0.00"))
    rate = Decimal(plan.customer_fee_rate or Decimal("0.0000"))
    flat = Decimal(plan.customer_fee_flat or Decimal("0.00"))
    return _quant(flat + (principal_amount * rate))



def user_has_purchased_product(user, product: Product) -> bool:
    return OrderItem.objects.filter(
        order__user=user,
        order__status__in=[Order.Status.PAID, Order.Status.FULFILLED],
        product=product,
    ).exists()


def refresh_product_rating(product: Product):
    agg = Review.objects.filter(product=product).aggregate(avg=Avg("rating"), cnt=Count("id"))
    avg = float(agg["avg"] or 0)
    cnt = int(agg["cnt"] or 0)

    product.rating = round(avg, 1)
    product.rating_count = cnt
    product.save(update_fields=["rating", "rating_count"])



def compute_pricing(
    *,
    subtotal: Decimal,
    coupon=None,
    has_physical: bool = True,
    tax_rate: Decimal = TAX_RATE,
    shipping_flat: Decimal = FLAT_SHIPPING,
) -> dict:
    """
    Single source of truth for all money math.
    """

    subtotal = _quant(subtotal)

    # ---- discount
    discount = Decimal("0.00")
    if coupon:
        if coupon.discount_type == Coupon.PERCENT:
            discount = subtotal * Decimal(coupon.value) / Decimal("100")
        else:
            discount = Decimal(coupon.value)

        discount = min(discount, subtotal)

    discount = _quant(discount)

    discounted_subtotal = _quant(subtotal - discount)

    # ---- shipping
    shipping = _quant(shipping_flat if has_physical else Decimal("0.00"))

    # ---- tax (usually on discounted subtotal)
    tax = _quant(discounted_subtotal * tax_rate)

    # ---- final payable
    grand_total = _quant(discounted_subtotal + shipping + tax)

    return {
        "subtotal": subtotal,
        "discount": discount,
        "discounted_subtotal": discounted_subtotal,
        "shipping": shipping,
        "tax": tax,
        "grand_total": grand_total,
    }


def _compute_totals(cart: Cart) -> dict:
    subtotal = sum(
        ci.quantity * ci.product.price
        for ci in cart.items.select_related("product")
    )

    has_physical = cart.items.filter(product__is_digital=False).exists()
    coupon = cart.coupon

    return compute_pricing(
        subtotal=subtotal,
        coupon=coupon,
        has_physical=has_physical,
    )

    #return {"subtotal": subtotal, "discount": discount, "tax": tax_rate_amt, "shipping": FLAT_SHIPPING, "grand": grand}


def _cart_to_dict(cart: Cart) -> dict:
    items = []
    subtotal = Decimal("0.00")
    has_physical = False

    for it in cart.items.select_related("product").prefetch_related("product__images"):
        line = (Decimal(it.quantity) * it.product.price).quantize(Decimal("0.01"))

        has_physical = it.product.is_digital is False

        first_img = it.product.images.first()
        image_url = first_img.product_image.url if first_img and first_img.product_image else None
        
        items.append({
            "id": str(it.id),
            "image_url": image_url,
            "product_id": str(it.product_id),
            "title": it.product.title,
            "price": str(it.product.price),
            "quantity": it.quantity,
            "line_total": str(line),
            "type": getattr(it.product, "type", "physical"),  # ✅ ensure frontend knows type
        })
        subtotal += line

    subtotal = subtotal.quantize(Decimal("0.01"))

    usable_coupon = cart.coupon if (cart.coupon and is_coupon_usable(cart.coupon)) else None
    pricing = compute_pricing(
        subtotal=subtotal,
        coupon=usable_coupon,
        has_physical=has_physical,
    )

    return {
        "id": str(cart.id),
        "items": items,
        "coupon": usable_coupon.code if usable_coupon else None,
        "subtotal": str(pricing["subtotal"]),
        "discount_total": str(pricing["discount"]),
        "grand_total": str(pricing["discounted_subtotal"]),
        "shipping_total": str(pricing["shipping"]),
        "tax_total": str(pricing["tax"]),
        "payable_total": str(pricing["grand_total"]),
    }

