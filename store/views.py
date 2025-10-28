from __future__ import annotations

import heapq
from itertools import islice
from decimal import Decimal
from datetime import datetime

from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination

from rest_framework_api_key.permissions import HasAPIKey

from api.authentication import SessionTokenAuthentication
from api.models import SessionToken
from accounts.models import User
from core.utils import _resolve_org
from billing.models import SubscriptionPayment, SubscriptionInvoice

# Store-related models
from store.models import (
    Category,
    Product,
    ProductImage,
    Review,
    Cart,
    CartItem,
    Coupon,
    Address,
    Order,
    OrderItem,
    Payment,
    Entitlement,
    BNPLPlanTemplate,
    BNPLAgreement,
    BNPLInstallment,
    Shipment,
    ShipmentItem,
    TrackingEvent,
    ReturnAuthorization,
    ReturnItem,
    ShippingCarrier,
    ShippingMethod,
)

# ---------- helpers ----------

def _get_session_token_from_request(request) -> str | None:
    token = request.META.get("HTTP_X_SESSION_TOKEN")
    if not token:
        auth = request.META.get("HTTP_AUTHORIZATION", "")
        if auth.startswith("Session "):
            token = auth[len("Session "):].strip()
    return token

def _get_user_from_request(request) -> User | None:
    # SessionTokenAuthentication should already set request.user.
    # Fallback in case you need it:
    token = _get_session_token_from_request(request)
    if not token:
        return getattr(request, "user", None)
    try:
        st = SessionToken.objects.select_related("user").get(key=token, is_active=True)
        return st.user
    except SessionToken.DoesNotExist:
        return getattr(request, "user", None)

def _get_or_create_cart(request) -> Cart:
    user = _get_user_from_request(request)
    session_key = request.session.session_key or request.session.save() or request.session.session_key
    qs = Cart.objects.filter(active=True)
    cart = None
    if user and qs.filter(user=user).exists():
        cart = qs.get(user=user)
    elif qs.filter(session_key=session_key).exists():
        cart = qs.get(session_key=session_key)
    else:
        cart = Cart.objects.create(user=user, session_key=session_key, active=True)
    # If user logs in later, attach:
    if user and cart.user_id is None:
        cart.user = user
        cart.save(update_fields=["user"])
    return cart

def _product_to_dict(p: Product, request=None) -> dict:
    first_image = p.images.order_by("sort_order").first()
    image_url = first_image.get_absolute_url(request) if first_image else None

    return {
        "id": str(p.id),
        "title": p.title,
        "slug": p.slug,
        "type": p.product_type,
        "category": p.category.name,
        "price": str(p.price),
        "rating": float(p.rating),
        "rating_count": p.rating_count,
        "image": image_url,
        "bnpl_enabled": getattr(p, "bnpl_enabled", True),
        "description":p.description
    }

def _cart_to_dict(cart: Cart) -> dict:
    items = []
    subtotal = Decimal("0.00")
    for it in cart.items.select_related("product"):
        line = it.quantity * it.product.price
        items.append({
            "id": str(it.id),
            "product_id": str(it.product_id),
            "title": it.product.title,
            "price": str(it.product.price),
            "quantity": it.quantity,
            "line_total": str(line),
        })
        subtotal += line
    return {
        "id": str(cart.id),
        "items": items,
        "coupon": cart.coupon.code if cart.coupon else None,
        "subtotal": str(subtotal),
    }


# ---------- catalog ----------

@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def categories_list(request):
    data = [{"id": str(c.id), "name": c.name, "slug": c.slug, "parent": str(c.parent_id) if c.parent_id else None}
            for c in Category.objects.order_by("name")]
    return Response({"results": data})



class CustomPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100

@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def products_list(request):
    """
    Query params: q, category, type, sort[=popular|rating|price_asc|price_desc|newest], min_price, max_price
    """
    q = (request.GET.get("q") or "").strip()
    category = request.GET.get("category")
    ptype = request.GET.get("type")
    sort = request.GET.get("sort") or "popular"
    min_p = request.GET.get("min_price")
    max_p = request.GET.get("max_price")

    qs = Product.objects.filter(is_active=True).select_related("category").prefetch_related("images")

    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(description__icontains=q))
    if category:
        qs = qs.filter(category__slug=category)
    if ptype:
        qs = qs.filter(product_type=ptype)
    if min_p:
        qs = qs.filter(price__gte=Decimal(min_p))
    if max_p:
        qs = qs.filter(price__lte=Decimal(max_p))

    if sort == "popular":
        qs = qs.order_by("-rating_count")
    elif sort == "rating":
        qs = qs.order_by("-rating", "-rating_count")
    elif sort == "price_asc":
        qs = qs.order_by("price")
    elif sort == "price_desc":
        qs = qs.order_by("-price")
    elif sort == "newest":
        qs = qs.order_by("-created_at")

    paginator = CustomPagination()
    page = paginator.paginate_queryset(qs, request)
    data = [_product_to_dict(p, request) for p in page]
    return paginator.get_paginated_response({"results": data})

@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def product_detail(request, slug: str):
    try:
        p = (Product.objects
             .select_related("category")
             .prefetch_related("images")
             .get(slug=slug, is_active=True))
    except Product.DoesNotExist:
        return Response({"detail": "Not found."}, status=404)
    return Response(_product_to_dict(p, request))

# ---------- cart ----------

@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def cart_get(request):
    cart = _get_or_create_cart(request)
    return Response(_cart_to_dict(cart))


@api_view(["POST"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def cart_add(request):
    """
    Body: {product_id, quantity}
    """
    try:
        print(" cndjkcndkcndknkfjvnfkvjnkjf ")
        print(request.data)
        cart = _get_or_create_cart(request)
        pid = request.data.get("product_id")
        qty = int(request.data.get("quantity") or 1)
        print(cart," cart " ,pid," pid " ,qty, " qttyyyy")
        if not pid:
            return Response({"detail": "Product ID is required."}, status=status.HTTP_400_BAD_REQUEST)
        print(pid)
        try:
            product = Product.objects.get(pk=pid, is_active=True)
        except Product.DoesNotExist:
            print("ppppppppp")
            return Response({"detail": "Invalid product."}, status=status.HTTP_400_BAD_REQUEST)

        item, created = CartItem.objects.get_or_create(
            cart=cart,
            product=product,
            defaults={"quantity": qty},
        )
        print(item, " item")

        if not created:
            item.quantity = F("quantity") + qty
            item.save(update_fields=["quantity"])
            item.refresh_from_db()
        print(_cart_to_dict(cart))
        return Response(_cart_to_dict(cart), status=status.HTTP_201_CREATED)

    except ValueError:
        print(e)
        return Response({"detail": "Invalid quantity value."}, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        print(e)
        # Catch any unexpected errors
        return Response(
            {"detail": f"An unexpected error occurred: {str(e)}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(["PATCH"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def cart_update_item(request, item_id: str):
    """
    Body: {quantity}
    """
    cart = _get_or_create_cart(request)
    try:
        item = cart.items.get(pk=item_id)
    except CartItem.DoesNotExist:
        return Response({"detail": "Item not found."}, status=404)
    qty = int(request.data.get("quantity") or 1)
    if qty < 1:
        item.delete()
    else:
        item.quantity = qty
        item.save(update_fields=["quantity"])
    return Response(_cart_to_dict(cart))

@api_view(["DELETE"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def cart_remove_item(request, item_id: str):
    cart = _get_or_create_cart(request)
    cart.items.filter(pk=item_id).delete()
    return Response(_cart_to_dict(cart))

@api_view(["POST"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def cart_apply_coupon(request):
    """
    Body: {code}
    """
    cart = _get_or_create_cart(request)
    code = (request.data.get("code") or "").strip().upper()
    try:
        coupon = Coupon.objects.get(code=code, active=True)
    except Coupon.DoesNotExist:
        return Response({"detail": "Invalid coupon."}, status=400)
    cart.coupon = coupon
    cart.save(update_fields=["coupon"])
    return Response(_cart_to_dict(cart))

# ---------- addresses ----------

@api_view(["GET", "POST"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def address_list_create(request):
    user = _get_user_from_request(request)
    if not user:
        return Response({"detail": "Auth required."}, status=401)

    if request.method == "GET":
        data = [{
            "id": str(a.id), "full_name": a.full_name, "line1": a.line1, "line2": a.line2,
            "city": a.city, "state": a.state, "postal_code": a.postal_code, "country": a.country,
            "phone": a.phone, "is_default": a.is_default
        } for a in user.addresses.all().order_by("-is_default", "full_name")]
        return Response({"results": data})

    # POST create
    payload = request.data
    addr = Address.objects.create(
        user=user,
        full_name=payload.get("full_name", ""),
        line1=payload.get("line1", ""),
        line2=payload.get("line2", ""),
        city=payload.get("city", ""),
        state=payload.get("state", ""),
        postal_code=payload.get("postal_code", ""),
        country=payload.get("country", "US"),
        phone=payload.get("phone", ""),
        is_default=bool(payload.get("is_default", False)),
    )
    return Response({"id": str(addr.id)}, status=201)

@api_view(["PATCH", "DELETE"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def address_update_delete(request, address_id: str):
    user = _get_user_from_request(request)
    try:
        addr = user.addresses.get(pk=address_id)
    except Address.DoesNotExist:
        return Response({"detail": "Not found."}, status=404)

    if request.method == "DELETE":
        addr.delete()
        return Response(status=204)

    for f in ["full_name","line1","line2","city","state","postal_code","country","phone","is_default"]:
        if f in request.data:
            setattr(addr, f, request.data.get(f))
    addr.save()
    return Response({"detail": "Updated."})
# ---------- checkout / orders / payments ----------

def _compute_totals(cart: Cart) -> dict:
    subtotal = sum((ci.quantity * ci.product.price for ci in cart.items.select_related("product")), Decimal("0.00"))
    discount = Decimal("0.00")
    if cart.coupon:
        if cart.coupon.discount_type == Coupon.PERCENT:
            discount = (subtotal * cart.coupon.value / Decimal("100")).quantize(Decimal("0.01"))
        else:
            discount = min(cart.coupon.value, subtotal)
    tax = Decimal("0.00")  # plug your tax calc
    shipping = Decimal("0.00")  # compute based on address/method later
    grand = max(subtotal - discount + tax + shipping, Decimal("0.00")).quantize(Decimal("0.01"))
    return {"subtotal": subtotal, "discount": discount, "tax": tax, "shipping": shipping, "grand": grand}

@api_view(["POST"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
@transaction.atomic
def checkout_create_order(request):
    """
    Body: {billing_address_id?, shipping_address_id?}
    Creates Order + OrderItems from the active cart.
    """
    user = _get_user_from_request(request)
    cart = _get_or_create_cart(request)
    if not cart.items.exists():
        return Response({"detail": "Cart is empty."}, status=400)

    has_physical = cart.items.filter(product__is_digital=False).exists()
    billing_id = request.data.get("billing_address_id")
    shipping_id = request.data.get("shipping_address_id") if has_physical else None

    billing = Address.objects.filter(user=user, id=billing_id).first() if billing_id else None
    shipping = Address.objects.filter(user=user, id=shipping_id).first() if shipping_id else None

    totals = _compute_totals(cart)
    order = Order.objects.create(
        user=user,
        subtotal=totals["subtotal"],
        discount_total=totals["discount"],
        tax_total=totals["tax"],
        shipping_total=totals["shipping"],
        grand_total=totals["grand"],
        coupon_code=cart.coupon.code if cart.coupon else "",
        billing_address=billing,
        shipping_address=shipping,
        status=Order.Status.PENDING,
    )
    for ci in cart.items.select_related("product"):
        OrderItem.objects.create(
            order=order,
            product=ci.product,
            title_snapshot=ci.product.title,
            unit_price=ci.product.price,
            quantity=ci.quantity,
            line_total=(ci.quantity * ci.product.price),
        )
    # leave cart as-is until payment success, or clear here if you prefer
    return Response({"order_id": str(order.id), "grand_total": str(order.grand_total)}, status=201)

@api_view(["POST"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def payment_card_start(request, order_id: str):
    """
    Body: {provider="stripe"|"paystack", currency?}
    """
    provider = (request.data.get("provider") or "stripe").lower()
    try:
        order = Order.objects.get(pk=order_id, status__in=[Order.Status.PENDING, Order.Status.PAID])
    except Order.DoesNotExist:
        return Response({"detail": "Order not found."}, status=404)

    pay = Payment.objects.create(
        order=order,
        provider=provider,
        status=Payment.Status.INITIATED,
        amount=order.grand_total,
        currency=request.data.get("currency") or "NGN",
    )
    # Return client secret / authorization_url as needed (mock here)
    return Response({"payment_id": str(pay.id), "status": pay.status, "amount": str(pay.amount)})

@api_view(["POST"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def payment_mark_captured(request, payment_id: str):
    """
    Body: {provider_ref?}
    (Webhook or admin action) Mark a card payment captured.
    """
    try:
        pay = Payment.objects.select_related("order").get(pk=payment_id)
    except Payment.DoesNotExist:
        return Response({"detail": "Not found."}, status=404)

    pay.status = Payment.Status.CAPTURED
    pay.provider_ref = request.data.get("provider_ref", "")
    pay.save(update_fields=["status", "provider_ref"])

    order = pay.order
    if order.status == Order.Status.PENDING:
        order.status = Order.Status.PAID
        order.save(update_fields=["status"])

        # grant entitlements for digital items
        for oi in order.items.select_related("product"):
            if oi.product.is_digital:
                Entitlement.objects.get_or_create(user=order.user, product=oi.product)

    return Response({"detail": "Captured.", "order_status": order.status})

# ---------- BNPL ----------

@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def bnpl_plans(request):
    plans = BNPLPlanTemplate.objects.filter(active=True).order_by("provider", "name")
    data = [{
        "id": str(p.id), "provider": p.provider, "name": p.name,
        "num_installments": p.num_installments, "interval_days": p.interval_days,
        "currency": p.currency, "min_amount": str(p.min_amount), "max_amount": str(p.max_amount) if p.max_amount else None
    } for p in plans]
    return Response({"results": data})

@api_view(["POST"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def bnpl_start(request, order_id: str):
    """
    Body: {plan_id}
    Returns: agreement_id
    """
    try:
        order = Order.objects.get(pk=order_id, status=Order.Status.PENDING)
    except Order.DoesNotExist:
        return Response({"detail": "Order not found or not pending."}, status=404)
    try:
        plan = BNPLPlanTemplate.objects.get(pk=request.data.get("plan_id"), active=True)
    except BNPLPlanTemplate.DoesNotExist:
        return Response({"detail": "Invalid plan."}, status=400)

    if plan.currency != "NGN":  # example check
        return Response({"detail": "Unsupported currency for this plan."}, status=400)
    if order.grand_total < plan.min_amount or (plan.max_amount and order.grand_total > plan.max_amount):
        return Response({"detail": "Order not eligible for plan limits."}, status=400)

    total = order.grand_total  # add customer fees here if applicable

    ag = BNPLAgreement.objects.create(
        order=order,
        plan=plan,
        provider=plan.provider,
        status=BNPLAgreement.Status.PENDING,
        num_installments=plan.num_installments,
        interval_days=plan.interval_days,
        take_downpayment_now=plan.take_downpayment_now,
        currency=plan.currency,
        principal_amount=order.grand_total,
        customer_fee_flat=plan.customer_fee_flat,
        customer_fee_rate=plan.customer_fee_rate,
        total_amount=total,
    )

    # Mock provider approval straight away:
    ag.status = BNPLAgreement.Status.ACTIVE
    ag.provider_checkout_id = f"chk_{ag.id}"
    ag.provider_agreement_id = f"agr_{ag.id}"
    ag.save(update_fields=["status", "provider_checkout_id", "provider_agreement_id"])
    ag.initialize_schedule(first_charge_at=timezone.now())

    # capture first installment immediately if flagged
    first = ag.installments.order_by("index").first()
    if first and first.capture_immediately:
        first.mark_captured(first.amount_due)
        if order.status == Order.Status.PENDING:
            order.status = Order.Status.PAID
            order.save(update_fields=["status"])
            # grant digital entitlements
            for oi in order.items.select_related("product"):
                if oi.product.is_digital:
                    Entitlement.objects.get_or_create(user=order.user, product=oi.product)

    return Response({"agreement_id": str(ag.id), "status": ag.status})

@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def bnpl_agreement_detail(request, agreement_id: str):
    try:
        ag = BNPLAgreement.objects.prefetch_related("installments").get(pk=agreement_id)
    except BNPLAgreement.DoesNotExist:
        return Response({"detail": "Not found."}, status=404)
    data = {
        "id": str(ag.id), "order_id": str(ag.order_id), "provider": ag.provider,
        "status": ag.status, "total_amount": str(ag.total_amount),
        "amount_paid": str(ag.amount_paid), "amount_outstanding": str(ag.amount_outstanding),
        "installments": [{
            "id": str(inst.id), "index": inst.index, "due_at": inst.due_at.isoformat(),
            "amount_due": str(inst.amount_due), "amount_paid": str(inst.amount_paid),
            "status": inst.status
        } for inst in ag.installments.order_by("index")]
    }
    return Response(data)

# ---------- orders ----------

@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def orders_list(request):
    user = _get_user_from_request(request)
    if not user:
        return Response({"detail": "Auth required."}, status=401)
    orders = (Order.objects.filter(user=user)
              .prefetch_related("items__product", "shipments")
              .order_by("-created_at")[:50])
    data = [{
        "id": str(o.id),
        "status": o.status,
        "grand_total": str(o.grand_total),
        "created_at": o.created_at.isoformat(),
        "items": [{"title": it.title_snapshot, "qty": it.quantity, "price": str(it.unit_price)} for it in o.items.all()],
    } for o in orders]
    return Response({"results": data})

@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def order_detail(request, order_id: str):
    user = _get_user_from_request(request)
    try:
        o = (Order.objects
             .select_related("billing_address","shipping_address")
             .prefetch_related("items__product","shipments__events","shipments__items__order_item")
             .get(pk=order_id, user=user))
    except Order.DoesNotExist:
        return Response({"detail": "Not found."}, status=404)

    shipments = []
    for s in o.shipments.all().order_by("-created_at"):
        shipments.append({
            "id": str(s.id), "status": s.status,
            "tracking_number": s.tracking_number, "tracking_url": s.tracking_url,
            "shipped_at": s.shipped_at.isoformat() if s.shipped_at else None,
            "delivered_at": s.delivered_at.isoformat() if s.delivered_at else None,
            "events": [{
                "code": e.event_code, "desc": e.description, "at": e.occurred_at.isoformat(),
                "city": e.city, "state": e.state, "country": e.country
            } for e in s.events.all().order_by("occurred_at")]
        })

    data = {
        "id": str(o.id), "status": o.status, "grand_total": str(o.grand_total),
        "items": [{"title": it.title_snapshot, "qty": it.quantity, "price": str(it.unit_price)} for it in o.items.all()],
        "shipments": shipments,
    }
    return Response(data)

# ---------- reviews ----------

@api_view(["POST"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def review_create(request, product_id: str):
    user = _get_user_from_request(request)
    if not user:
        return Response({"detail": "Auth required."}, status=401)
    rating = int(request.data.get("rating") or 0)
    title = request.data.get("title","")
    body = request.data.get("body","")
    try:
        product = Product.objects.get(pk=product_id, is_active=True)
    except Product.DoesNotExist:
        return Response({"detail": "Invalid product."}, status=400)

    rv, created = Review.objects.update_or_create(
        product=product, user=user,
        defaults={"rating": rating, "title": title, "body": body}
    )
    # You likely have a signal to recompute Product.rating & rating_count
    return Response({"id": str(rv.id), "detail": "Saved."}, status=201 if created else 200)

# ---------- returns (RMA) ----------

@api_view(["POST"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def rma_create(request, order_id: str):
    user = _get_user_from_request(request)
    reason = request.data.get("reason", "")
    try:
        order = Order.objects.get(pk=order_id, user=user)
    except Order.DoesNotExist:
        return Response({"detail": "Not found."}, status=404)
    rma = ReturnAuthorization.objects.create(
        order=order, reason=reason, status=ReturnAuthorization.Status.REQUESTED,
        rma_number=f"RMA-{order.id.hex[:6]}-{int(timezone.now().timestamp())}"
    )
    items = request.data.get("items", [])  # [{order_item_id, quantity, reason}]
    for it in items:
        try:
            oi = order.items.get(pk=it.get("order_item_id"))
        except OrderItem.DoesNotExist:
            continue
        ReturnItem.objects.create(
            rma=rma, order_item=oi, quantity=int(it.get("quantity") or 1), reason=it.get("reason","")
        )
    return Response({"rma_id": str(rma.id), "rma_number": rma.rma_number}, status=201)

@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def entitlements_list(request):
    user = _get_user_from_request(request)
    if not user:
        return Response({"detail": "Auth required."}, status=401)
    ents = Entitlement.objects.filter(user=user).select_related("product")
    data = [{"product_id": str(e.product_id), "title": e.product.title} for e in ents]
    return Response({"results": data})



# ---------- helpers ----------

def _shipment_to_dict(s: Shipment) -> dict:
    return {
        "id": str(s.id),
        "order_id": str(s.order_id),
        "status": s.status,
        "carrier": s.carrier.code if s.carrier_id else None,
        "method": s.method.name if s.method_id else None,
        "tracking_number": s.tracking_number,
        "tracking_url": s.tracking_url,
        "label_url": s.label_url,
        "label_cost": str(s.label_cost),
        "currency": s.currency,
        "to": {
            "name": s.to_name, "line1": s.to_line1, "line2": s.to_line2,
            "city": s.to_city, "state": s.to_state, "postal_code": s.to_postal_code,
            "country": s.to_country, "phone": s.to_phone, "email": s.email,
        },
        "shipped_at": s.shipped_at.isoformat() if s.shipped_at else None,
        "delivered_at": s.delivered_at.isoformat() if s.delivered_at else None,
        "items": [{
            "order_item_id": str(si.order_item_id),
            "title": si.order_item.title_snapshot,
            "quantity": si.quantity,
        } for si in s.items.select_related("order_item").all()],
        "events": [{
            "id": str(e.id),
            "code": e.event_code,
            "desc": e.description,
            "occurred_at": e.occurred_at.isoformat(),
            "city": e.city, "state": e.state, "country": e.country, "postal_code": e.postal_code,
            "carrier_status": e.carrier_status,
        } for e in s.events.order_by("occurred_at", "created_at")],
    }

def _advance_shipment_status_from_event(shipment: Shipment, event_code: str, occurred_at):
    code = event_code
    now = occurred_at or timezone.now()
    prev = shipment.status

    if code in (TrackingEvent.EventCode.INFO_RECEIVED, TrackingEvent.EventCode.ACCEPTED, TrackingEvent.EventCode.IN_TRANSIT):
        if shipment.status in [Shipment.Status.PENDING, Shipment.Status.READY]:
            shipment.status = Shipment.Status.IN_TRANSIT
            shipment.shipped_at = shipment.shipped_at or now
    elif code == TrackingEvent.EventCode.OUT_FOR_DELIVERY:
        shipment.status = Shipment.Status.OUT_FOR_DELIVERY
    elif code == TrackingEvent.EventCode.DELIVERED:
        shipment.status = Shipment.Status.DELIVERED
        shipment.delivered_at = now
    elif code in (TrackingEvent.EventCode.EXCEPTION, TrackingEvent.EventCode.FAILURE):
        shipment.status = Shipment.Status.EXCEPTION
    elif code == TrackingEvent.EventCode.RETURNED:
        shipment.status = Shipment.Status.RETURNED

    if shipment.status != prev:
        shipment.save(update_fields=["status", "shipped_at", "delivered_at"])

# ---------- customer-facing: list & detail ----------

@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def order_shipments_list(request, order_id: str):
    """
    Authenticated customer: see all shipments for their order.
    """
    user = getattr(request, "user", None)
    try:
        order = Order.objects.get(pk=order_id, user=user)
    except Order.DoesNotExist:
        return Response({"detail": "Not found."}, status=404)

    shipments = order.shipments.select_related("carrier", "method").prefetch_related("items__order_item", "events")
    return Response({"results": [_shipment_to_dict(s) for s in shipments.order_by("-created_at")]})

@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def shipment_detail(request, shipment_id: str):
    """
    Authenticated customer: see a single shipment with its timeline.
    """
    user = getattr(request, "user", None)
    try:
        s = (Shipment.objects
             .select_related("order", "carrier", "method")
             .prefetch_related("items__order_item", "events")
             .get(pk=shipment_id, order__user=user))
    except Shipment.DoesNotExist:
        return Response({"detail": "Not found."}, status=404)
    return Response(_shipment_to_dict(s))

# ---------- customer-style tracking by tracking_number (still protected by your API key/session) ----------

@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def track_by_number(request):
    """
    Query params: tracking_number=...  (optional: last4=ZIP code or postal_code for an extra check)
    Returns the shipment + events if found and belongs to the authenticated user.
    """
    tn = (request.GET.get("tracking_number") or "").strip()
    if not tn:
        return Response({"detail": "tracking_number is required."}, status=400)

    user = getattr(request, "user", None)
    try:
        s = (Shipment.objects
             .select_related("order", "carrier", "method")
             .prefetch_related("items__order_item", "events")
             .get(tracking_number=tn, order__user=user))
    except Shipment.DoesNotExist:
        return Response({"detail": "Not found."}, status=404)

    # Optional ZIP check (if you pass ?last4=1234)
    last4 = (request.GET.get("last4") or "").strip()
    if last4 and (s.to_postal_code or "").replace(" ", "")[-4:] != last4:
        return Response({"detail": "Not found."}, status=404)

    return Response(_shipment_to_dict(s))

# ---------- staff/ops: create shipments, attach items, set tracking ----------

@api_view(["POST"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
@transaction.atomic
def shipment_create(request, order_id: str):
    """
    Staff-only (simple check): create a parcel and optionally attach order items.
    Body: {
      "carrier_code": "ups"|"usps"|...,
      "method_id": "<uuid>"?,          # optional
      "to": {name,line1,line2?,city,state?,postal_code?,country,phone?,email?},
      "items": [{"order_item_id": "...", "quantity": 1}, ...]
    }
    """
    if not getattr(request.user, "is_staff", False):
        return Response({"detail": "Forbidden."}, status=403)

    try:
        order = Order.objects.get(pk=order_id)
    except Order.DoesNotExist:
        return Response({"detail": "Order not found."}, status=404)

    carrier_code = (request.data.get("carrier_code") or "other").lower()
    try:
        carrier = ShippingCarrier.objects.get(code=carrier_code)
    except ShippingCarrier.DoesNotExist:
        return Response({"detail": "Invalid carrier."}, status=400)

    method = None
    if request.data.get("method_id"):
        try:
            method = ShippingMethod.objects.get(pk=request.data["method_id"], carrier=carrier)
        except ShippingMethod.DoesNotExist:
            return Response({"detail": "Invalid shipping method."}, status=400)

    to = request.data.get("to") or {}
    s = Shipment.objects.create(
        order=order,
        status=Shipment.Status.READY,
        carrier=carrier,
        method=method,
        to_name=to.get("name", order.shipping_address.full_name if order.shipping_address else ""),
        to_line1=to.get("line1", order.shipping_address.line1 if order.shipping_address else ""),
        to_line2=to.get("line2", order.shipping_address.line2 if order.shipping_address else ""),
        to_city=to.get("city", order.shipping_address.city if order.shipping_address else ""),
        to_state=to.get("state", order.shipping_address.state if order.shipping_address else ""),
        to_postal_code=to.get("postal_code", order.shipping_address.postal_code if order.shipping_address else ""),
        to_country=to.get("country", order.shipping_address.country if order.shipping_address else "US"),
        to_phone=to.get("phone", order.shipping_address.phone if order.shipping_address else ""),
        email=to.get("email", ""),
    )

    for it in request.data.get("items", []):
        try:
            oi = order.items.get(pk=it.get("order_item_id"))
        except OrderItem.DoesNotExist:
            continue
        qty = max(1, int(it.get("quantity") or 1))
        ShipmentItem.objects.create(shipment=s, order_item=oi, quantity=qty)

    return Response(_shipment_to_dict(s), status=201)

@api_view(["POST"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def shipment_set_tracking(request, shipment_id: str):
    """
    Staff-only: attach a tracking number / label (mock 'buy label').
    Body: {tracking_number, tracking_url?, label_url?, label_cost?, currency?}
    """
    if not getattr(request.user, "is_staff", False):
        return Response({"detail": "Forbidden."}, status=403)

    try:
        s = Shipment.objects.get(pk=shipment_id)
    except Shipment.DoesNotExist:
        return Response({"detail": "Not found."}, status=404)

    s.tracking_number = request.data.get("tracking_number", s.tracking_number)
    s.tracking_url = request.data.get("tracking_url", s.tracking_url)
    s.label_url = request.data.get("label_url", s.label_url)
    if "label_cost" in request.data:
        s.label_cost = request.data.get("label_cost") or s.label_cost
    if "currency" in request.data:
        s.currency = request.data.get("currency") or s.currency
    if s.status in [Shipment.Status.PENDING, Shipment.Status.READY]:
        s.status = Shipment.Status.IN_TRANSIT
        s.shipped_at = s.shipped_at or timezone.now()
    s.save()
    return Response(_shipment_to_dict(s))

@api_view(["POST"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def shipment_update_status(request, shipment_id: str):
    """
    Staff-only manual override.
    Body: {status: pending|ready|in_transit|out_for_delivery|delivered|exception|returned|cancelled}
    """
    if not getattr(request.user, "is_staff", False):
        return Response({"detail": "Forbidden."}, status=403)

    try:
        s = Shipment.objects.get(pk=shipment_id)
    except Shipment.DoesNotExist:
        return Response({"detail": "Not found."}, status=404)

    new_status = request.data.get("status")
    valid = {c for c, _ in Shipment.Status.choices}
    if new_status not in valid:
        return Response({"detail": "Invalid status."}, status=400)

    s.status = new_status
    if new_status == Shipment.Status.DELIVERED:
        s.delivered_at = timezone.now()
    s.save(update_fields=["status", "delivered_at"])
    return Response(_shipment_to_dict(s))

# ---------- tracking events (manual + webhook) ----------

@api_view(["POST"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def shipment_add_event(request, shipment_id: str):
    """
    Staff-only: add a tracking event.
    Body: {event_code, description?, occurred_at?, city?, state?, country?, postal_code?, carrier_status?}
    """
    if not getattr(request.user, "is_staff", False):
        return Response({"detail": "Forbidden."}, status=403)

    try:
        s = Shipment.objects.select_related("carrier").get(pk=shipment_id)
    except Shipment.DoesNotExist:
        return Response({"detail": "Not found."}, status=404)

    code = request.data.get("event_code")
    valid = {c for c, _ in TrackingEvent.EventCode.choices}
    if code not in valid:
        return Response({"detail": "Invalid event_code."}, status=400)

    occurred = request.data.get("occurred_at")
    occurred_dt = timezone.now() if not occurred else timezone.datetime.fromisoformat(occurred)
    e = TrackingEvent.objects.create(
        shipment=s,
        event_code=code,
        description=request.data.get("description", ""),
        city=request.data.get("city",""),
        state=request.data.get("state",""),
        country=request.data.get("country",""),
        postal_code=request.data.get("postal_code",""),
        occurred_at=occurred_dt,
        carrier_status=request.data.get("carrier_status",""),
        raw_payload=None,
    )
    _advance_shipment_status_from_event(s, code, occurred_dt)
    return Response({"id": str(e.id)}, status=201)

@api_view(["POST"])
@permission_classes([HasAPIKey])                   # Keep API key; session token not required for carriers—still accepted.
@authentication_classes([SessionTokenAuthentication])
def tracking_webhook(request):
    """
    Minimal normalized webhook to ingest carrier updates.
    Body: {
      "tracking_number": "...",
      "event_code": "in_transit|out_for_delivery|delivered|exception|returned|accepted|info_received|failure",
      "description": "...",
      "occurred_at": ISO8601,
      "city": "...", "state": "...", "country": "US", "postal_code": "...",
      "carrier_status": "..."
    }
    """
    tn = (request.data.get("tracking_number") or "").strip()
    code = request.data.get("event_code")
    if not tn or not code:
        return Response({"detail": "tracking_number and event_code are required."}, status=400)

    try:
        s = Shipment.objects.get(tracking_number=tn)
    except Shipment.DoesNotExist:
        return Response({"detail": "Shipment not found."}, status=404)

    occurred = request.data.get("occurred_at")
    occurred_dt = timezone.now() if not occurred else timezone.datetime.fromisoformat(occurred)

    e = TrackingEvent.objects.create(
        shipment=s,
        event_code=code,
        description=request.data.get("description", ""),
        city=request.data.get("city",""),
        state=request.data.get("state",""),
        country=request.data.get("country",""),
        postal_code=request.data.get("postal_code",""),
        occurred_at=occurred_dt,
        carrier_status=request.data.get("carrier_status",""),
        raw_payload=request.data,   # keep raw for debugging
    )
    _advance_shipment_status_from_event(s, code, occurred_dt)
    return Response({"detail": "ok", "event_id": str(e.id)}, status=202)
