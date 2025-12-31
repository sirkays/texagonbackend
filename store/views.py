from __future__ import annotations

import heapq
from itertools import islice
from decimal import Decimal
from datetime import datetime
from django.db import transaction
from django.db.models import F, Q, Prefetch
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

from .utils import (calc_discount, is_coupon_usable,_bnpl_customer_fees,_quant,_to_bool)
from texagonbackend.settings import TAX_RATE,FLAT_SHIPPING
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

from .bnpl import pick_plan_for_product, check_eligibility, compute_bnpl_breakdown

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
        "price": str(p.price) ,
        "rating": float(p.rating),
        "rating_count": p.rating_count,
        "image": image_url,
        "bnpl_enabled": getattr(p, "bnpl_enabled", True),
        "description":p.description
    }




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
    discount_total = calc_discount(subtotal, usable_coupon).quantize(Decimal("0.01"))

    # ✅ discounted subtotal (your previous grand_total)
    grand_total = (subtotal - discount_total).quantize(Decimal("0.01"))

    # ✅ shipping & tax on backend
    shipping_total = (FLAT_SHIPPING if has_physical else Decimal("0.00")).quantize(Decimal("0.01"))
    tax_total = (grand_total * TAX_RATE).quantize(Decimal("0.01"))

    # ✅ final payable amount
    payable_total = (grand_total + shipping_total + tax_total).quantize(Decimal("0.01"))
    #print(shipping_total, " sipping ",payable_total, " grand ", grand_total, " dis ", discount_total, " subtotal ", subtotal)
    return {
        "id": str(cart.id),
        "items": items,
        "coupon": usable_coupon.code if usable_coupon else None,
        "subtotal": str(subtotal),
        "discount_total": str(discount_total),
        "grand_total": str(grand_total),            # subtotal - discount
        "shipping_total": str(shipping_total),      # backend shipping
        "tax_total": str(tax_total),                # backend tax
        "payable_total": str(payable_total),        # grand + shipping + tax
        
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
        cart = _get_or_create_cart(request)
        pid = request.data.get("product_id")
        qty = int(request.data.get("quantity") or 1)
        if not pid:
            return Response({"detail": "Product ID is required."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            product = Product.objects.get(pk=pid, is_active=True)
        except Product.DoesNotExist:
            return Response({"detail": "Invalid product."}, status=status.HTTP_400_BAD_REQUEST)

        item, created = CartItem.objects.get_or_create(
            cart=cart,
            product=product,
            defaults={"quantity": qty},
        )

        if not created:
            item.quantity = F("quantity") + qty
            item.save(update_fields=["quantity"])
            item.refresh_from_db()
        return Response(_cart_to_dict(cart), status=status.HTTP_201_CREATED)

    except ValueError:
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
    cart = _get_or_create_cart(request)
    code = (request.data.get("code") or "").strip().upper()

    try:
        coupon = Coupon.objects.get(code=code, active=True)
    except Coupon.DoesNotExist:
        return Response({"detail": "Invalid coupon."}, status=400)

    if not is_coupon_usable(coupon):
        return Response({"detail": "Coupon is not usable (expired/not started/limit reached)."}, status=400)

    cart.coupon = coupon
    cart.save(update_fields=["coupon"])

    cart_data = _cart_to_dict(cart)
    
    request.session['grand_total'] = cart_data['grand_total']
    request.session['subtotal'] = cart_data['subtotal']
    request.session['discount_total'] = cart_data['discount_total']
    request.session['payable_total'] = cart_data['payable_total']
    return Response(cart_data)



# ---------- addresses ----------

@api_view(["GET", "POST"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def address_list_create(request):
    try:
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
    except Exception as e:
        print(e)
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
    tax_rate_amt = (subtotal * TAX_RATE).quantize(Decimal("0.01"))
    grand = max(subtotal - discount + tax_rate_amt + FLAT_SHIPPING, Decimal("0.00")).quantize(Decimal("0.01"))
    return {"subtotal": subtotal, "discount": discount, "tax": tax_rate_amt, "shipping": FLAT_SHIPPING, "grand": grand}





@api_view(["POST"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
@transaction.atomic
def checkout_create_order(request):
    try:
        """
        Body (normal/cart):
        { billing_address_id?, shipping_address_id? }

        Body (BNPL / request-item):
        {
            "is_bnpl": true,
            "product_id": "<uuid>",
            "quantity": 1,
            "bnpl_plan_id": "<uuid>"  # optional
        }

        Creates Order + OrderItems.
        - Normal flow: from active cart items
        - BNPL flow: from product_id + quantity (cart can be empty)
        Also creates BNPLAgreement for BNPL flow.
        """
        user = _get_user_from_request(request)
        cart = _get_or_create_cart(request)

        is_bnpl = _to_bool(request.data.get("is_bnpl"))
        product_id = (request.data.get("product_id") or "").strip()
        quantity = int(request.data.get("quantity") or 1)
        bnpl_plan_id = request.data.get("bnpl_plan_id")

        if quantity < 1:
            return Response({"detail": "quantity must be >= 1"}, status=status.HTTP_400_BAD_REQUEST)

        # ============================================================
        # BNPL PATH (request-item) — cart can be empty
        # ============================================================
        if is_bnpl:
            if not product_id:
                return Response({"detail": "product_id is required for BNPL."}, status=status.HTTP_400_BAD_REQUEST)

            try:
                product = Product.objects.select_related("default_bnpl_plan").prefetch_related("images").get(
                    id=product_id, is_active=True
                )
            except Product.DoesNotExist:
                return Response({"detail": "Product not found."}, status=status.HTTP_404_NOT_FOUND)

            if not product.bnpl_enabled:
                return Response({"detail": "BNPL is not enabled for this product."}, status=status.HTTP_400_BAD_REQUEST)

            # Pick plan
            if bnpl_plan_id:
                plan = BNPLPlanTemplate.objects.filter(id=bnpl_plan_id, active=True).first()
                if not plan:
                    return Response({"detail": "Invalid bnpl_plan_id."}, status=status.HTTP_400_BAD_REQUEST)
            else:
                plan = None
                if product.default_bnpl_plan and product.default_bnpl_plan.active:
                    plan = product.default_bnpl_plan
                if not plan:
                    plan = BNPLPlanTemplate.objects.filter(active=True).first()

            if not plan:
                return Response({"detail": "No BNPL plan configured."}, status=status.HTTP_400_BAD_REQUEST)

            # Address (optional; shipping only for physical)
            has_physical = (product.is_digital is False)

            billing_id = request.data.get("billing_address_id")
            shipping_id = request.data.get("shipping_address_id") if has_physical else None

            billing = Address.objects.filter(user=user, id=billing_id).first() if billing_id else None
            shipping = Address.objects.filter(user=user, id=shipping_id).first() if shipping_id else None

            # ---------------------------
            # Totals
            # ---------------------------
            # Principal base is product price * qty
            line_subtotal = _quant((product.price or Decimal("0.00")) * Decimal(quantity))

            # Decide whether BNPL includes tax/shipping:
            # Most BNPL providers finance the TOTAL payable (including tax/shipping).
            tax_total = _quant(line_subtotal * TAX_RATE)
            shipping_total = _quant(FLAT_SHIPPING if has_physical else Decimal("0.00"))

            discount_total = Decimal("0.00")  # request-item ignores coupons unless you support it
            grand_total = _quant(line_subtotal - discount_total + tax_total + shipping_total)

            # Create Order
            order = Order.objects.create(
                user=user,
                subtotal=line_subtotal,
                discount_total=_quant(discount_total),
                tax_total=tax_total,
                shipping_total=shipping_total,
                grand_total=grand_total,
                coupon_code="",
                billing_address=billing,
                shipping_address=shipping,
                status=Order.Status.PENDING,
            )

            OrderItem.objects.create(
                order=order,
                product=product,
                title_snapshot=product.title,
                unit_price=_quant(product.price or Decimal("0.00")),
                quantity=quantity,
                line_total=_quant((product.price or Decimal("0.00")) * Decimal(quantity)),
            )

            # ---------------------------
            # Create BNPLAgreement
            # ---------------------------
            principal_amount = _quant(order.grand_total)  # finance the full payable amount
            customer_fees = _bnpl_customer_fees(principal_amount, plan)
            total_amount = _quant(principal_amount + customer_fees)
            

            agreement = BNPLAgreement.objects.create(
                order=order,
                plan=plan,
                provider=plan.provider,
                status=BNPLAgreement.Status.PENDING,
                num_installments=plan.num_installments,
                interval_days=plan.interval_days,
                take_downpayment_now=plan.take_downpayment_now,
                currency=plan.currency or "NGN",
                principal_amount=principal_amount,
                customer_fee_flat=_quant(plan.customer_fee_flat or Decimal("0.00")),
                customer_fee_rate=Decimal(plan.customer_fee_rate or Decimal("0.0000")),
                total_amount=total_amount,
                amount_paid=Decimal("0.00"),
                amount_outstanding=total_amount,
            )

            # Create schedule rows now (installments)
            agreement.initialize_schedule(first_charge_at=timezone.now())

            # Pay-today (first installment) amount for frontend button
            first_inst = agreement.installments.order_by("index").first()
            pay_today = _quant(first_inst.amount_due if first_inst else Decimal("0.00"))

            first_img = product.images.first()
            image_url = first_img.get_absolute_url(request) if first_img else None

            return Response(
                {
                    "order_id": str(order.id),
                    "grand_total": str(order.grand_total),

                    "is_bnpl": True,
                    "product_id": str(product.id),
                    "quantity": quantity,
                    "bnpl_plan_id": str(plan.id),

                    "bnpl_agreement_id": str(agreement.id),
                    "bnpl_total_amount": str(agreement.total_amount),
                    "bnpl_customer_fees": str(customer_fees),
                    "bnpl_pay_today": str(pay_today),

                    "product_details": {
                        "image_url": image_url,
                        "product_id": str(product.id),
                        "title": product.title,
                        "price": str(product.price),
                    },

                    "installments": [
                        {
                            "index": inst.index,
                            "due_at": inst.due_at.isoformat(),
                            "amount_due": str(inst.amount_due),
                            "capture_immediately": bool(inst.capture_immediately),
                            "status": inst.status,
                        }
                        for inst in agreement.installments.all().order_by("index")
                    ],
                },
                status=status.HTTP_201_CREATED,
            )

        # ============================================================
        # NORMAL PATH (cart)
        # ============================================================
        if not cart.items.exists():
            return Response({"detail": "Cart is empty."}, status=status.HTTP_400_BAD_REQUEST)

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
                unit_price=_quant(ci.product.price or Decimal("0.00")),
                quantity=ci.quantity,
                line_total=_quant(Decimal(ci.quantity) * (ci.product.price or Decimal("0.00"))),
            )

        return Response({"order_id": str(order.id), "grand_total": str(order.grand_total)}, status=status.HTTP_201_CREATED)

    except Exception as e:
        print(e)



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




@api_view(["POST"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def bnpl_breakdown(request):
    """
    POST body:
    {
    "product_id": "<uuid>",           # required unless order_id provided
    "quantity": 1,                    # optional (default 1)
    "plan_id": "<uuid>",              # optional
    "order_id": "<uuid>"              # optional: if you want breakdown from an existing order total
    }
    """
    data = request.data or {}
    product_id = data.get("product_id")
    plan_id = data.get("plan_id")
    order_id = data.get("order_id")
    quantity = int(data.get("quantity") or 1)
    
    if quantity < 1:
        return Response({"detail": "quantity must be >= 1"}, status=status.HTTP_400_BAD_REQUEST)

    principal_amount = None
    product = None

    # Option A: from order
    if order_id:
        try:
            order = Order.objects.select_related("user").get(id=order_id)
        except Order.DoesNotExist:
            return Response({"detail": "Order not found."}, status=status.HTTP_404_NOT_FOUND)

        # ensure the session user owns it if you require
        if order.user_id and request.user.is_authenticated and order.user_id != request.user.id:
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)

        principal_amount = order.grand_total

    # Option B: from product * qty
    else:
        if not product_id:
            return Response({"detail": "product_id is required."}, status=status.HTTP_400_BAD_REQUEST)

        
        try:
            product = Product.objects.select_related("default_bnpl_plan").get(id=product_id, is_active=True)
        except Product.DoesNotExist:
            return Response({"detail": "Product not found."}, status=status.HTTP_404_NOT_FOUND)

        if not product.bnpl_enabled:
            return Response(
                {
                    "eligible": False,
                    "reason": "BNPL is not enabled for this product.",
                    "product_id": str(product.id),
                },
                status=status.HTTP_200_OK,
            )

        principal_amount = (product.price or Decimal("0.00")) * Decimal(quantity)

    plan = pick_plan_for_product(product, plan_id=plan_id) if product else (
        BNPLPlanTemplate.objects.filter(id=plan_id, active=True).first() if plan_id else BNPLPlanTemplate.objects.filter(active=True).first()
    )
    if not plan:
        return Response(
            {"eligible": False, "reason": "No BNPL plan configured."},
            status=status.HTTP_200_OK,
        )

    principal_amount = Decimal(principal_amount or Decimal("0.00"))
    eligible, reason = check_eligibility(principal_amount, plan)

    breakdown = compute_bnpl_breakdown(
        principal_amount=principal_amount,
        plan=plan,
        currency=plan.currency or "NGN",
    )
    has_physical = product.is_digital is False

    first_img = product.images.first()
    image_url = first_img.product_image.url if first_img and first_img.product_image else None

    request.session['first_payment'] = breakdown['downpayment_now']

    return Response(
        {
            "eligible": eligible,
            "reason": reason,
            "plan": {
                "id": str(plan.id),
                "provider": plan.provider,
                "name": plan.name,
                "num_installments": plan.num_installments,
                "interval_days": plan.interval_days,
                "take_downpayment_now": plan.take_downpayment_now,
                "currency": plan.currency,
                "min_amount": str(plan.min_amount),
                "max_amount": str(plan.max_amount) if plan.max_amount is not None else None,
                "customer_fee_flat": str(plan.customer_fee_flat),
                "customer_fee_rate": str(plan.customer_fee_rate),
            },
            "breakdown": breakdown,
            "product_details":{
                "image_url": image_url,
                "product_id": str(product.id),
                "title": product.title,
                "price": str(product.price),
            }
        },
        status=status.HTTP_200_OK,
    )

# ---------- orders ----------

@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def orders_list(request):
    user = _get_user_from_request(request)
    if not user:
        return Response({"detail": "Auth required."}, status=401)

    orders = (
    Order.objects.filter(user=user)
    .select_related("bnpl_agreement", "bnpl_agreement__plan")
    .prefetch_related("items__product", "shipments", "bnpl_agreement__installments")
    .order_by("-created_at")[:50]
    )

    data = []
    for o in orders:
        bnpl = getattr(o, "bnpl_agreement", None)

        next_payment = None
        remaining = None
        agreement_id = None

        if bnpl:
            agreement_id = str(bnpl.id)

            # simplest: compute from installments (pending/authorized)
            inst_qs = bnpl.installments.order_by("index")
            unpaid = [i for i in inst_qs if i.status in {"pending", "authorized", "failed"}]
            remaining = len(unpaid)

            if unpaid:
                next_payment = unpaid[0].due_at.isoformat()

        data.append({
            "id": str(o.id),
            "status": o.status,
            "grand_total": str(o.grand_total),
            "created_at": o.created_at.isoformat(),
            "items": [{"title": it.title_snapshot, "qty": it.quantity, "price": str(it.unit_price)} for it in o.items.all()],

            # ✅ BNPL fields for frontend
            "is_bnpl": bool(bnpl),
            "agreement_id": agreement_id,
            "bnpl_status": bnpl.status if bnpl else None,
            "bnpl_provider": bnpl.provider if bnpl else None,
            "next_payment": next_payment,
            "remaining_payments": remaining,
        })
    return Response({"results": data})



@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def order_detail(request, order_id: str):
    user = _get_user_from_request(request)
    try:
        o = (Order.objects
             .select_related("billing_address", "shipping_address", "bnpl_agreement")  # ✅ add
             .prefetch_related(
                 "items__product",
                 "shipments__events",
                 "shipments__items__order_item",
             )
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

    bnpl = getattr(o, "bnpl_agreement", None)

    data = {
        "id": str(o.id),
        "status": o.status,
        "grand_total": str(o.grand_total),
        "items": [{"title": it.title_snapshot, "qty": it.quantity, "price": str(it.unit_price)} for it in o.items.all()],
        "shipments": shipments,

        # ✅ BNPL extras for frontend
        "is_bnpl": bool(bnpl),
        "agreement_id": str(bnpl.id) if bnpl else None,
        "bnpl_status": bnpl.status if bnpl else None,
        "bnpl_provider": bnpl.provider if bnpl else None,
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
