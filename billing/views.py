from __future__ import annotations
import traceback
import heapq
import secrets
from datetime import datetime
from decimal import Decimal,Decimal, ROUND_HALF_UP, InvalidOperation
from itertools import islice
from django.db import models

from django.db import transaction
from django.db.models import F, Q, Case, When, DateTimeField
from django.shortcuts import get_object_or_404
from django.utils import dateparse, timezone

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, authentication_classes,parser_classes
from rest_framework.response import Response

from rest_framework_api_key.permissions import HasAPIKey

from api.authentication import SessionTokenAuthentication
from academics.models import ParentProfile
from billing.models import SubscriptionInvoice, SubscriptionPayment, InvoiceType
from core.utils import _is_org_admin_or_teacher, _resolve_org, get_object_or_404_ajax
from orgs.models import OrganizationMembership
from store.models import Order, Payment, OrderItem,CartItem,Cart
from .models import Complaint, ComplaintResponse,ComplaintAttachment
from .utils import confirm_transaction, generate_payment_link
from rest_framework.parsers import JSONParser, FormParser, MultiPartParser
from django.core.files.uploadedfile import UploadedFile
from live.models import TutoringBooking
from learning.models import Enrollment

MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024  # 25MB per file
ALLOWED_CONTENT_TYPES = None  # e.g. {"image/png","image/jpeg","application/pdf"}


# try to import nanoid; fallback if not installed
try:
    from nanoid import generate as nanoid_generate  # nanoid.generate(...)
    _have_nanoid = True
except Exception:
    _have_nanoid = False


def _generate_reference(length: int = 12) -> str:
    """
    Prefer nanoid if available, otherwise fallback to a URL-safe token.
    """
    if _have_nanoid:
        # nanoid.generate accepts (alphabet, size) or (size) depending on version.
        # We'll call two-arg variant with a safe alphanumeric alphabet.
        try:
            return nanoid_generate("0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ", length)
        except TypeError:
            # older/newer API might accept only size
            return nanoid_generate(length)
    # fallback
    return secrets.token_urlsafe(length)[:length]


def _user_active_membership_for_org(user, org):
    """Return an active OrganizationMembership for the user and org, or None."""
    return OrganizationMembership.objects.filter(user=user, organization=org, is_active=True).order_by("-id").first()


def _serialize_payment(payment: SubscriptionPayment,payment_link: str) -> dict:
    """Minimal serializer for responses (expand as needed)."""
    return {
        "payment_link":payment_link,
        "id": payment.pk,
        "reference": payment.reference,
        "invoice_id": payment.invoice_id,
        "amount": str(payment.amount),
        "currency": payment.currency,
        "method": payment.method,
        "status": payment.status,
        "paid_at": payment.paid_at.isoformat() if payment.paid_at else None,
        "meta": payment.meta,
        "created_at": payment.created_at.isoformat() if hasattr(payment, "created_at") else None,
        "updated_at": payment.updated_at.isoformat() if hasattr(payment, "updated_at") else None,
    }


@api_view(["POST"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
@transaction.atomic
def create_subscription_payment(request):
    try:
        """
        POST payload example:
        {
            "invoice": 123
        }

        Only a user attached to the organization (OrganizationMembership, is_active=True)
        of the invoice's subscription may create the payment.
        """
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return Response({"detail": "Invalid or missing session token."}, status=status.HTTP_401_UNAUTHORIZED)

        invoice_id = request.data.get("invoice") or request.data.get("invoice_id")
        redirect_url = request.data.get("redirect_url")

        is_store_payment = request.data.get("is_store_payment")

        bnpl_plan_id = request.data.get("bnpl_plan_id")

        is_bnpl = request.data.get("is_bnpl")

        item_list = (request.data.get("item_list") or "").split(",") if request.data.get("item_list") else []

        title = request.data.get("payment_title",  "Subscription Payment")

        invoice = None
        membership = None

        if is_store_payment:
            raw_amount = request.data.get("amount")
            order_id = request.data.get("order_id")

            order = get_object_or_404_ajax(Order, pk=order_id)
            if not order:
                return Response({"detail": "Order is not found"}, status=status.HTTP_400_BAD_REQUEST)

            if raw_amount in (None, "", 0, "0"):
                return Response({"detail": "Amount is not found"}, status=status.HTTP_400_BAD_REQUEST)

            try:
                amount = Decimal(str(raw_amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            except (InvalidOperation, TypeError, ValueError):
                return Response({"detail": "Invalid amount"}, status=status.HTTP_400_BAD_REQUEST)

            membership = get_object_or_404_ajax(OrganizationMembership, user=request.user)
            if membership is False:
                membership = OrganizationMembership.fetch_defaults()

            invoice, _created = SubscriptionInvoice.objects.get_or_create(
                organization_membership=membership,
                status="pending",
                defaults={"amount": amount, "due_at": timezone.now()},
            )

            if is_bnpl and bnpl_plan_id:
                invoice_type, _ = InvoiceType.objects.get_or_create(
                    invoice=invoice,
                    invoice_type="store",
                    defaults={"object_id": order_id, "object_type": "bnpl",
                    "meta":{"bnpl_plan_id": bnpl_plan_id}
                    },
                )
            else:
                invoice_type, _ = InvoiceType.objects.get_or_create(
                    invoice=invoice,
                    invoice_type="store",
                    defaults={"object_id": order_id, "object_type": "order"},
                )


        if not redirect_url:
            return Response({"detail": "Missing 'redirect_url' in request body."}, status=status.HTTP_400_BAD_REQUEST)


        if not invoice_id and not invoice:
            return Response({"detail": "Missing 'invoice' id in request body."}, status=status.HTTP_400_BAD_REQUEST)

        # load invoice with subscription -> organization
        if not invoice:
            try:
                invoice = SubscriptionInvoice.objects.select_related("subscription__organization").get(number=invoice_id)
            except SubscriptionInvoice.DoesNotExist:
                return Response({"detail": "Invoice not found."}, status=status.HTTP_404_NOT_FOUND)
        

        if not membership:
            org = invoice.subscription.organization

            # check membership
            membership = _user_active_membership_for_org(user, org)
            if membership is None:
                return Response({"detail": "User is not attached to the invoice's organization."}, status=status.HTTP_403_FORBIDDEN)

        # Build payment object
        reference = _generate_reference(12)
        amount = invoice.amount
        currency = getattr(invoice, "currency", "NGN")

        # Create the payment. method left blank intentionally.
        payment = SubscriptionPayment(
            invoice=invoice,
            reference=reference,
            amount=amount,
            currency=currency,
            redirect_url=redirect_url,
            method="",  # left blank per your spec
            status=SubscriptionPayment.Status.CREATED,
            # paid_at defaults to timezone.now() per your model; we can leave it as default
            meta={"created_by_membership_id": membership.pk, "created_by_user_id": user.pk},
        )

        try:
            payment.save()
            if invoice.subscription:
                payment_plan = invoice.subscription.plan.name
            else:
                payment_plan = f"Store payment for {item_list}"

            customer_detail = {
                "email":request.user.email,
            }
            payment_link = generate_payment_link(
                request,
                request.user.id, 
                reference, redirect_url,
                title,customer_detail,
                amount,payment_plan
            )
        except Exception as e:
            return Response({"detail": "Could not create payment.", "error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(_serialize_payment(payment, payment_link), status=status.HTTP_201_CREATED)

    except Exception as e:
        print(e)
    return Response({"detail": "Could not create payment.", "error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["PATCH"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def update_subscription_payment(request, reference):
    """
    PATCH endpoint to update status, method and paid_at of a SubscriptionPayment.
    Only users attached to the payment's organization may update.

    Example PATCH body:
    {
        "status": "success",
        "method": "card",
        "paid_at": "2025-09-14T16:30:00Z"   # optional, parsed if provided
    }
    """
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return Response({"detail": "Invalid or missing session token."}, status=status.HTTP_401_UNAUTHORIZED)

    payment = get_object_or_404(SubscriptionPayment.objects.select_related("invoice__subscription__organization"), reference=reference)
    org = payment.invoice.subscription.organization
    membership = _user_active_membership_for_org(user, org)
    if membership is None:
        return Response({"detail": "User is not attached to the payment's organization."}, status=status.HTTP_403_FORBIDDEN)
    allowed_fields = {"status", "method", "paid_at"}
    payload_keys = set(request.data.keys()) & allowed_fields
    if not payload_keys:
        return Response({"detail": f"Provide one of the updatable fields: {', '.join(sorted(allowed_fields))}."},
                        status=status.HTTP_400_BAD_REQUEST)

    status_value = request.data.get("status")
    if status_value is not None:
        valid_statuses = {choice[0] for choice in SubscriptionPayment.Status.choices}
        if status_value not in valid_statuses:
            return Response({"detail": f"Invalid status '{status_value}'. Valid: {', '.join(valid_statuses)}"},
                            status=status.HTTP_400_BAD_REQUEST)
        payment.status = status_value

    # method (free-form)
    if "method" in request.data:
        payment.method = request.data.get("method", "") or ""

    # paid_at: parse ISO datetime if provided
    if "paid_at" in request.data:
        paid_at_raw = request.data.get("paid_at")
        if paid_at_raw in (None, ""):
            # if user explicitly wants to clear it, set now (model does not permit null)
            payment.paid_at = timezone.now()
        else:
            parsed = parse_datetime(paid_at_raw)
            if parsed is None:
                return Response({"detail": "Could not parse 'paid_at'. Provide ISO 8601 datetime string."},
                                status=status.HTTP_400_BAD_REQUEST)
            # ensure timezone-aware
            if timezone.is_naive(parsed):
                parsed = timezone.make_aware(parsed)
            payment.paid_at = parsed

    try:
        payment.save()
    except Exception as e:
        return Response({"detail": "Could not update payment.", "error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    return Response(_serialize_payment(payment, "None"), status=status.HTTP_200_OK)


@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def fetch_parent_invoices(request):
    """
    GET /api/parent/invoices/?status=paid&invoice_type=subscription&search=Y0Y

    Filters:
      - organization_membership == the parent's membership
      - optional: ?status=<open,paid,void,uncollectible,active>
      - optional: ?invoice_type=<tutor,subscription>
      - optional: ?search=<string>  (searches invoice number + invoice type info)
    """
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return Response(
            {"detail": "Invalid or missing session token."},
            status=status.HTTP_401_UNAUTHORIZED
        )

    parent_profile = getattr(user, "parent_profile", None)
    if parent_profile is None:
        return Response(
            {"detail": "Parent profile not found for this user."},
            status=status.HTTP_404_NOT_FOUND
        )

    membership = (
        OrganizationMembership.objects
        .filter(
            user=user,
            organization=parent_profile.organization,
            role=OrganizationMembership.Role.PARENT,
        )
        .order_by("-id")
        .first()
    )
    if not membership:
        return Response(
            {"detail": "Membership profile not found for this user."},
            status=status.HTTP_404_NOT_FOUND
        )

    # -----------------------
    # Optional filters
    # -----------------------
    status_param = (request.query_params.get("status") or "").strip()
    if status_param:
        valid_statuses = {choice[0] for choice in SubscriptionInvoice.Status.choices}
        if status_param not in valid_statuses:
            return Response(
                {"detail": f"Invalid status '{status_param}'. Valid: {', '.join(sorted(valid_statuses))}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

    invoice_type_param = (request.query_params.get("invoice_type") or "").strip()
    if invoice_type_param:
        valid_types = {choice[0] for choice in InvoiceType.Paytype.choices}
        if invoice_type_param not in valid_types:
            return Response(
                {"detail": f"Invalid invoice_type '{invoice_type_param}'. Valid: {', '.join(sorted(valid_types))}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

    search_param = (request.query_params.get("search") or "").strip()

    # -----------------------
    # Query
    # -----------------------
    qs = (
        SubscriptionInvoice.objects
        .select_related(
            "subscription__organization",
            "organization_membership",
            "invoicetype",
        )
        .filter(organization_membership=membership)
        .order_by("-issued_at")
    )

    if status_param:
        qs = qs.filter(status=status_param)

    if invoice_type_param:
        qs = qs.filter(invoicetype__invoice_type=invoice_type_param)

    # ✅ Add search
    if search_param:
        # Keep search strict to avoid false matches
        qs = qs.filter(
            Q(number__icontains=search_param) |
            Q(status__icontains=search_param) |
            Q(invoicetype__invoice_type__icontains=search_param) |
            Q(invoicetype__object_type__icontains=search_param) |
            Q(invoicetype__object_id__isnull=False, invoicetype__object_id__icontains=search_param)
        )

    # -----------------------
    # Serializer
    # -----------------------
    def _serialize(inv: SubscriptionInvoice):
        inv_type_obj = getattr(inv, "invoicetype", None)

        invoice_type_value = (
            inv_type_obj.invoice_type
            if inv_type_obj and inv_type_obj.invoice_type
            else InvoiceType.Paytype.SUBSCRIPTION
        )

        return {
            "id": inv.pk,
            "number": inv.number,
            "subscription_id": inv.subscription_id,
            "organization_id": inv.subscription.organization_id if inv.subscription_id else None,
            "organization_membership_id": inv.organization_membership_id,
            "amount": str(inv.amount),
            "currency": inv.currency,
            "issued_at": inv.issued_at.isoformat() if inv.issued_at else None,
            "due_at": inv.due_at.isoformat() if inv.due_at else None,
            "status": inv.status,
            "invoice_type": invoice_type_value,
            "invoice_type_object_id": getattr(inv_type_obj, "object_id", None),
            "invoice_type_object_type": getattr(inv_type_obj, "object_type", None),
            "meta": inv.meta or {},
            "created_at": getattr(inv, "created_at", None).isoformat() if getattr(inv, "created_at", None) else None,
            "updated_at": getattr(inv, "updated_at", None).isoformat() if getattr(inv, "updated_at", None) else None,
        }

    data = [_serialize(inv) for inv in qs]
    return Response({"count": len(data), "results": data}, status=status.HTTP_200_OK)


@api_view(["POST"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
@transaction.atomic
def confirm_payment(request):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return Response({"detail": "Invalid or missing session token."}, status=status.HTTP_401_UNAUTHORIZED)

    parent_profile = getattr(user, "parent_profile", None)
    if parent_profile is None:
        return Response({"detail": "Parent profile not found for this user."}, status=status.HTTP_404_NOT_FOUND)

    # Try to find the parent membership (prefer any membership record regardless of is_active,
    # because invoices are historical and we may still want to show them).
    membership = (
        OrganizationMembership.objects
        .filter(user=user, organization=parent_profile.organization, role=OrganizationMembership.Role.PARENT)
        .order_by("-id")
        .first()
    )

    transaction_id = request.data.get("transaction_id")
    tx_ref = request.data.get("tx_ref")
    invoice_id = request.data.get("invoice_id")
    
    subscription_payment = get_object_or_404_ajax(
        SubscriptionPayment, reference=tx_ref, 
        invoice__organization_membership__user=request.user,
    )

    if subscription_payment.invoice.number != invoice_id and str(subscription_payment.invoice.id) != invoice_id:
        return Response({"detail": "Could not update payment, wrong invoice"}, status=status.HTTP_400_BAD_REQUEST)
        
    if subscription_payment is False:
        return Response({"detail": "We could not confirm payment at this moment. Contact support if it not confirmed in 30min"}, 
        status=status.HTTP_404_NOT_FOUND)

    subscription_payment.transaction_id = transaction_id
    subscription_payment.save()
    res = confirm_transaction(transaction_id)
    
    if res[1] != "success":
        messages.warning(request, 
        f'Something went wrong, Contact support at info@texagon.epichouse.online with transaction no. {transaction_id}')
        subscription_payment.change_current_trans("failed")
        return Response({"detail": "Could not update payment, because we could not confirm payment"}, status=status.HTTP_400_BAD_REQUEST)

    subscription_payment.change_current_trans("success")

    subscription_payment.invoice.status = "paid"
    subscription_payment.invoice.transaction_id = transaction_id
    subscription_payment.invoice.save()

    try:
        invoice_type = InvoiceType.objects.get(invoice=subscription_payment.invoice)
        if invoice_type.invoice_type == "tutor":
            if invoice_type.object_type == "booking":
                booking = TutoringBooking.objects.get(pk=invoice_type.object_id)
                if booking:
                    booking.status = "confirmed"
                    booking.save()

                    course = booking.private_tutoring.course

                    Enrollment.objects.get_or_create(
                        student=booking.student,
                        course=course,
                        status="active"
                    )
        elif invoice_type.invoice_type == "store":
            if invoice_type.object_type == "order":
                order = get_object_or_404_ajax(Order, pk=invoice_type.object_id, user=request.user)
                if order is False:
                    return Response({"status":"failed", "message":"Order not found"}, status=status.HTTP_400_BAD_REQUEST)
                order.status = "paid"
                order.save()

                products = OrderItem.objects.filter(order=order).values_list("product")

                carts = CartItem.objects.filter(product__pk__in=products, cart__user=request.user)
                if carts:
                    carts.delete()

                    Cart.objects.filter(user=request.user).update(coupon=None)

                    if order.coupon_code:
                        coupon = get_object_or_404_ajax(Coupon, code=order.coupon_code)
                        if coupon:
                            coupon.used_count = coupon.used_count + 1

                            coupon.save()

            elif invoice_type.object_type == "bnpl":
                order = get_object_or_404_ajax(Order, pk=invoice_type.object_id, user=request.user)
                if not order:
                    return Response({"status":"failed", "message":"Order not found"}, status=status.HTTP_400_BAD_REQUEST)

                # 1) Mark order as paid OR keep pending + bnpl flag (your call)
                order.status = "paid"
                order.save(update_fields=["status"])

                # 2) Create / ensure BNPLAgreement exists
                agreement = getattr(order, "bnpl_agreement", None)
                if agreement is None:
                    # You MUST know what plan was chosen.
                    # Ideally you stored plan on the order at create-order time.
                    # Example: store chosen plan id in order.notes or a dedicated field.
                    plan_id = invoice_type.meta.get('bnpl_plan_id')
                    plan = BNPLPlanTemplate.objects.filter(id=plan_id, active=True).first()
                    if not plan:
                        return Response({"detail": "BNPL plan missing for this order."}, status=400)

                    agreement = BNPLAgreement.objects.create(
                        order=order,
                        plan=plan,
                        provider=plan.provider,
                        status=BNPLAgreement.Status.PENDING,

                        num_installments=plan.num_installments,
                        interval_days=plan.interval_days,
                        take_downpayment_now=plan.take_downpayment_now,

                        currency=plan.currency or "NGN",
                        principal_amount=order.grand_total,
                        customer_fee_flat=plan.customer_fee_flat,
                        customer_fee_rate=plan.customer_fee_rate,
                        total_amount=order.grand_total,  # or principal + fee if that’s your design

                        provider_checkout_id=subscription_payment.reference,  # optional mapping
                    )

                # 3) Initialize schedule if not already created
                if not agreement.installments.exists():
                    agreement.initialize_schedule()

                # 4) Mark installment #1 as captured using the paid amount
                # The payment amount is subscription_payment.amount (invoice.amount)
                first_inst = agreement.installments.order_by("index").first()
                if not first_inst:
                    return Response({"detail": "BNPL schedule missing."}, status=500)

                first_inst.mark_captured(subscription_payment.amount)

                # 5) Clear cart items (same as your normal order flow)
                products = OrderItem.objects.filter(order=order).values_list("product", flat=True)
                CartItem.objects.filter(product__pk__in=list(products), cart__user=request.user).delete()
                Cart.objects.filter(user=request.user).update(coupon=None)

                # 6) Coupon used_count handling (optional — but keep consistent with normal checkout)
                if order.coupon_code:
                    coupon = get_object_or_404_ajax(Coupon, code=order.coupon_code)
                    if coupon:
                        coupon.used_count = coupon.used_count + 1
                        coupon.save()

    except Exception as e:
        print(e)
        return Response({"status":"failed", "message":f"{e}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    return Response({"status":"success"}, status=status.HTTP_200_OK)



@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def transactions_list(request):
    try:
        user = request.user
        if not user or not user.is_authenticated:
            return Response({"detail": "User not authenticated"}, status=401)

        org, msg = _resolve_org(request)
        if not org:
            return Response({"detail": "Organization not found"}, status=400)

        # --- Read type param ---
        type_param = request.query_params.get("type")
        if type_param not in (None, "store", "subscription"):
            return Response({"detail": "Invalid type. Must be 'store' or 'subscription'."}, status=400)

        # --- Build base querysets ---
        sub_qs = SubscriptionPayment.objects.filter(
            invoice__organization_membership__user=user,
            invoice__subscription__organization=org,
        ).select_related(
            "invoice",
            "invoice__subscription",
            "invoice__organization_membership",
            "invoice__organization_membership__user",
        ).annotate(
            effective_date=Case(
                When(status="success", then=F("paid_at")),
                default=F("created_at"),
                output_field=DateTimeField(),
            )
        )

        store_qs = Payment.objects.filter(order__user=user).select_related(
            "order", "order__user"
        ).annotate(effective_date=F("created_at"))
        # --- Apply filters common to both ---
        status_param = request.query_params.get("status")
        if status_param:
            sub_qs = sub_qs.filter(status=status_param)
            store_qs = store_qs.filter(status=status_param)

        from_date_str = request.query_params.get("from_date")
        if from_date_str:
            try:
                from_date = timezone.make_aware(datetime.strptime(from_date_str, "%Y-%m-%d"))
            except ValueError:
                return Response({"detail": "Invalid from_date format. Use YYYY-MM-DD."}, status=400)
            sub_qs = sub_qs.filter(created_at__gte=from_date)
            store_qs = store_qs.filter(created_at__gte=from_date)

        to_date_str = request.query_params.get("to_date")
        if to_date_str:
            try:
                to_date = timezone.make_aware(
                    datetime.strptime(to_date_str, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
                )
            except ValueError:
                return Response({"detail": "Invalid to_date format. Use YYYY-MM-DD."}, status=400)
            sub_qs = sub_qs.filter(created_at__lte=to_date)
            store_qs = store_qs.filter(created_at__lte=to_date)

        search = request.query_params.get("search")
        if search:
            sub_qs = sub_qs.filter(
                Q(reference__icontains=search)
                | Q(invoice__number__icontains=search)
                | Q(transaction_id__icontains=search)
            )
            store_qs = store_qs.filter(
                Q(provider_ref__icontains=search) | Q(error_message__icontains=search)
            )

        # --- Apply type filter ---
        if type_param == "subscription":
            qs = sub_qs.order_by("-effective_date")
        elif type_param == "store":
            qs = store_qs.order_by("-effective_date")
        else:
            # merge both if no type specified
            sub_qs = sub_qs.order_by("-effective_date")
            store_qs = store_qs.order_by("-effective_date")
            qs = heapq.merge(
                sub_qs.iterator(chunk_size=1000),
                store_qs.iterator(chunk_size=1000),
                key=lambda p: p.effective_date,
                reverse=True,
            )

        # --- Pagination ---
        try:
            page_number = int(request.query_params.get("page", 1))
            page_size = int(request.query_params.get("page_size", 10))
        except ValueError:
            return Response({"detail": "Invalid page or page_size."}, status=400)
        if page_number < 1 or page_size < 1:
            return Response({"detail": "Page and page_size must be positive."}, status=400)

        start = (page_number - 1) * page_size
        end = start + page_size

        if type_param in ("store", "subscription"):
            paginated = qs[start:end]
        else:
            paginated = list(islice(qs, start, end))

        # --- Serialize ---
        data = []
        for pay in paginated:
            if isinstance(pay, SubscriptionPayment):
                date = pay.paid_at if pay.status == "success" else pay.created_at
                customer_email = (
                    pay.invoice.organization_membership.user.email
                    if pay.invoice.organization_membership
                    else ""
                )
                d = {
                    "id": pay.id,
                    "type": "subscription",
                    "reference": pay.reference,
                    "amount": str(pay.amount),
                    "currency": pay.currency,
                    "status": pay.status,
                    "date": (date or pay.created_at).isoformat(),
                    "customer": customer_email,
                    "invoice_number": pay.invoice.number,
                }
            else:  # Payment (store)
                date = pay.created_at
                customer_email = pay.order.user.email if pay.order and pay.order.user else ""
                d = {
                    "id": str(pay.id),
                    "type": "store",
                    "reference": pay.provider_ref,
                    "amount": str(pay.amount),
                    "currency": pay.currency,
                    "status": pay.status,
                    "date": (date or pay.effective_date).isoformat(),
                    "customer": customer_email,
                    "order_id": str(pay.order.id) if pay.order else "",
                }
            data.append(d)

        if type_param == "subscription":
            count = sub_qs.count()
        elif type_param == "store":
            count = store_qs.count()
        else:
            count = sub_qs.count() + store_qs.count()

        num_pages = (count + page_size - 1) // page_size

        return Response(
            {
                "count": count,
                "num_pages": num_pages,
                "page": page_number,
                "results": data,
            }
        )

    except Exception as e:
        import traceback
        traceback.print_exc()
        return Response({"detail": f"An unexpected error occurred: {str(e)}"}, status=500)

# -----------------------------
# Serialization helpers
# -----------------------------

# -----------------------------
# Helpers (safe for your schema)
# -----------------------------
def _human(user) -> str | None:
    if not user:
        return None
    fn = getattr(user, "get_full_name", None)
    full = (fn() if callable(fn) else "") or ""
    full = full.strip()
    return full or getattr(user, "email", None) or getattr(user, "username", None)


def _resp_dict(r: ComplaintResponse) -> dict:
    author = getattr(r, "author_name", None) or ""
    return {
        "id": str(r.id),
        "message": r.message,
        "author": author,
        "role": r.role,
        "created_at": r.created_at.isoformat(),
    }


def _complaint_org_id(c: Complaint):
    """
    Infer organization_id ONLY for subscription complaints, since:
      Complaint -> subscription_payment -> invoice -> subscription -> organization_id
    For purchase complaints, Order has no org FK in your project; return None.
    """
    if c.subscription_payment_id:
        try:
            return c.subscription_payment.invoice.subscription.organization_id
        except Exception:
            return None
    return None


def _attachment_dict(a: ComplaintAttachment) -> dict:
    try:
        url = a.file.url
    except Exception:
        url = None
    return {
        "id": str(a.id),
        "original_name": a.original_name or (getattr(a.file, "name", "") or ""),
        "content_type": a.content_type,
        "uploaded_at": a.uploaded_at.isoformat(),
        "file_url": url,  # will be absolutized in responses
        "uploaded_by": _human(getattr(a, "uploaded_by", None)),
    }


def _complaint_dict(c: Complaint) -> dict:
    # Transaction info
    tx = None
    if c.payment_id:
        tx = {
            "type": "purchase",
            "payment_id": str(c.payment_id),
            "provider_ref": getattr(c.payment, "provider_ref", None),
            "order_id": getattr(c.payment, "order_id", None),  # Payment.order is OneToOne
        }
    elif c.subscription_payment_id:
        tx = {
            "type": "subscription",
            "reference": getattr(c.subscription_payment, "reference", None),
            "invoice": getattr(getattr(c.subscription_payment, "invoice", None), "number", None),
        }

    return {
        "id": str(c.id),
        "title": c.title,
        "description": c.description,
        "status": c.status,
        "priority": c.priority,
        "assigned_to": _human(getattr(c, "assigned_to", None)),
        "created_at": c.created_at.isoformat(),
        "updated_at": c.updated_at.isoformat(),
        "transaction": tx,
        "organization_id": _complaint_org_id(c),  # may be None for purchase complaints
        "responses": [_resp_dict(r) for r in c.responses.order_by("created_at")],
        "attachments": [_attachment_dict(a) for a in c.attachments.order_by("uploaded_at")],
    }


def _absolutize_attachment_urls(request, payload: dict | list[dict]):
    """
    Turn relative media URLs into absolute URLs on any payload(s) that contain 'attachments'.
    """
    if isinstance(payload, dict):
        items = [payload]
    else:
        items = payload

    for item in items:
        for a in item.get("attachments", []):
            if a.get("file_url"):
                a["file_url"] = request.build_absolute_uri(a["file_url"])


def _save_attachments_from_request(request, complaint: Complaint, uploader) -> list[ComplaintAttachment]:
    """
    Accepts multipart uploads under 'attachments' or 'attachments[]'.
    Validates size/type against MAX_ATTACHMENT_BYTES and ALLOWED_CONTENT_TYPES.
    """
    files = []
    if "attachments" in request.FILES:
        many = request.FILES.getlist("attachments")
        files = many if many else [request.FILES["attachments"]]
    elif "attachments[]" in request.FILES:
        files = request.FILES.getlist("attachments[]")

    created = []
    for f in files:
        if not isinstance(f, UploadedFile):
            continue
        if f.size and f.size > MAX_ATTACHMENT_BYTES:
            raise ValueError(f"Attachment '{getattr(f, 'name', '')}' exceeds max size of {MAX_ATTACHMENT_BYTES // (1024 * 1024)}MB.")
        if ALLOWED_CONTENT_TYPES and f.content_type not in ALLOWED_CONTENT_TYPES:
            raise ValueError(f"Attachment type '{f.content_type}' not allowed.")

        att = ComplaintAttachment.objects.create(
            complaint=complaint,
            file=f,  # respects complaint-aware upload_to path
            uploaded_by=uploader,
            original_name=getattr(f, "name", "") or "",
            content_type=getattr(f, "content_type", "") or "",
        )
        created.append(att)
    return created


# ======================================================
# Create Complaint
# ======================================================
@api_view(["POST"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
@parser_classes([MultiPartParser, FormParser, JSONParser])  # allow attachments at creation
def create_complaint(request):
    try:
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return Response({"detail": "Invalid or missing session token."}, status=status.HTTP_401_UNAUTHORIZED)

        # Optional org scoping (won't be written to Complaint because model has no org)
        org, org_err = _resolve_org(request)
        if org_err:
            return org_err

        # ---- Inputs ----
        # Normalize category; accept "order" as alias for "store" for backward-compat
        category = (request.data.get("category") or "").strip().lower()
        if category not in ("subscription", "order"):
            return Response(
                {"detail": "Invalid 'category'. Must be 'store' (or 'order') or 'subscription'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        title = (request.data.get("title") or "").strip()
        description = (request.data.get("description") or "").strip()
        priority = (request.data.get("priority") or "medium").lower()

        if not title or not description:
            return Response(
                {"detail": "Both 'title' and 'description' are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if priority not in ("low", "medium", "high"):
            return Response(
                {"detail": "Invalid 'priority'. Must be low|medium|high."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # Transaction identifiers
        payment_id = request.data.get("transaction_reference")
        sub_ref = request.data.get("subscription_reference")
        sub_txn_id = request.data.get("subscription_transaction_id")

        # ---- Resolve the linked transaction according to category ----
        payment = None
        sub_payment = None
        tx_org_id = None  # used only for subscription ownership/org checks
        if category == "order":
            # must provide payment_id and must NOT provide subscription identifiers
            if not payment_id:
                return Response(
                    {"detail": "For category 'store', 'payment_id' is required."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            payment = get_object_or_404(
                Payment.objects.select_related("order", "order__user"),
                provider_ref=payment_id
            )
            # Ownership check: ensure the payment belongs to this user
            if not payment.order or payment.order.user_id != user.id:
                return Response(
                    {"detail": "You do not have access to this payment."},
                    status=status.HTTP_403_FORBIDDEN,
                )
            # Note: Orders in this schema have no org FK, so we cannot enforce org equality for store.

        elif category == "subscription":
            # must provide either subscription_reference OR subscription_transaction_id
            if not payment_id:
                return Response(
                    {"detail": "For category 'subscription', do not provide 'payment_id'."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            
            sub_payment = (
                SubscriptionPayment.objects
                .select_related("invoice__subscription__organization",
                                "invoice__organization_membership__user")
                .filter(**({"reference": payment_id}))
                .first()
            )
            if not sub_payment:
                return Response({"detail": "Subscription transaction not found."},
                                status=status.HTTP_404_NOT_FOUND)

            # Ensure the subscription payment belongs to the requesting user
            inv_mem = getattr(sub_payment, "invoice", None)
            inv_mem = getattr(inv_mem, "organization_membership", None)
            inv_user_id = getattr(inv_mem, "user_id", None)
            if inv_user_id != user.id:
                return Response(
                    {"detail": "You do not have access to this subscription transaction."},
                    status=status.HTTP_403_FORBIDDEN,
                )
            # We CAN org-check for subscriptions
            tx_org_id = getattr(sub_payment.invoice.subscription, "organization_id", None)
            if org and tx_org_id:
                if tx_org_id != org.id:
                    return Response(
                        {"detail": "Transaction does not belong to this organization."},
                        status=status.HTTP_403_FORBIDDEN,
                    )
                if not OrganizationMembership.objects.filter(user=user, organization=org, is_active=True).exists():
                    return Response(
                        {"detail": "You do not have access to this organization."},
                        status=status.HTTP_403_FORBIDDEN,
                    )

        # ---- Create complaint + first message, then attachments ----
        with transaction.atomic():
            comp = Complaint.objects.create(
                title=title,
                description=description,
                priority=priority,
                status=Complaint.Status.OPEN,
                created_by=user,
                payment=payment if category == "store" else None,
                subscription_payment=sub_payment if category == "subscription" else None,
            )
            ComplaintResponse.objects.create(
                complaint=comp,
                message=description,
                author_name=user.get_full_name() or user.email,
                role="user",
            )

            try:
                _save_attachments_from_request(request, comp, user)
            except ValueError as ve:
                return Response({"detail": str(ve)}, status=status.HTTP_400_BAD_REQUEST)

        # ---- Reload with relations for response payload ----
        comp = (
            Complaint.objects.select_related(
                "payment__order",
                "subscription_payment__invoice__subscription__organization",
                "assigned_to",
            )
            .prefetch_related("responses", "attachments")
            .get(id=comp.id)
        )

        payload = _complaint_dict(comp)
        _absolutize_attachment_urls(request, payload)
        return Response(payload, status=status.HTTP_201_CREATED)

    except Exception as e:
        print("\n[ERROR] create_complaint():", str(e))
        traceback.print_exc()
        return Response(
            {"detail": "An unexpected error occurred.", "error": str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

# ======================================================
# List Complaints
# ======================================================
@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def list_complaints(request):
    try:
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return Response({"detail": "Invalid or missing session token."}, status=status.HTTP_401_UNAUTHORIZED)

        org, org_err = _resolve_org(request)  # optional
        if org_err:
            return org_err

        qs = (
            Complaint.objects
            .select_related(
                "payment__order",  # DO NOT traverse to non-existent order.organization
                "subscription_payment__invoice__subscription__organization",
                "assigned_to",
            )
            .prefetch_related("responses", "attachments")
            .order_by("-created_at")
        )

        # If org provided, we can only safely scope subscription-linked complaints to that org
        if org:
            qs = qs.filter(
                subscription_payment__invoice__subscription__organization_id=org.id
            )
            # (Purchase-only complaints will be excluded when org provided — by design)
            if not OrganizationMembership.objects.filter(user=user, organization=org, is_active=True).exists():
                return Response({"detail": "You do not have access to this organization."},
                                status=status.HTTP_403_FORBIDDEN)
        else:
            # No org provided: show all complaints tied to any orgs the user belongs to (subscription-linked),
            # plus purchase-only complaints created by the user (fallback visibility rule).
            org_ids = list(
                OrganizationMembership.objects.filter(user=user, is_active=True)
                .values_list("organization_id", flat=True)
            )
            if org_ids:
                qs = qs.filter(
                    models.Q(subscription_payment__invoice__subscription__organization_id__in=org_ids) |
                    models.Q(subscription_payment__isnull=True, created_by=user)
                )
            else:
                # user belongs to no orgs: show only their own purchase/general complaints
                qs = qs.filter(subscription_payment__isnull=True, created_by=user)

        # Filters
        status_q = (request.query_params.get("status") or "").lower()
        priority_q = (request.query_params.get("priority") or "").lower()
        tx_type = (request.query_params.get("transaction_type") or "").lower()
        search = (request.query_params.get("search") or "").strip()

        if status_q in Complaint.Status.values:
            qs = qs.filter(status=status_q)
        if priority_q in ("low", "medium", "high"):
            qs = qs.filter(priority=priority_q)
        if tx_type == "purchase":
            qs = qs.filter(payment__isnull=False)
        elif tx_type == "subscription":
            qs = qs.filter(subscription_payment__isnull=False)
        if search:
            qs = qs.filter(models.Q(title__icontains=search) | models.Q(description__icontains=search))

        data = [_complaint_dict(c) for c in qs[:200]]
        _absolutize_attachment_urls(request, data)
        return Response({"results": data}, status=status.HTTP_200_OK)

    except Exception as e:
        print("\n[ERROR] list_complaints():", str(e))
        traceback.print_exc()
        return Response({"detail": "An unexpected error occurred.", "error": str(e)},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ======================================================
# Retrieve Complaint
# ======================================================
@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def get_complaint(request, complaint_id: str):
    try:
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return Response({"detail": "Invalid or missing session token."}, status=status.HTTP_401_UNAUTHORIZED)

        org, org_err = _resolve_org(request)  # optional
        if org_err:
            return org_err

        c = get_object_or_404(
            Complaint.objects.select_related(
                "payment__order",
                "subscription_payment__invoice__subscription__organization",
                "assigned_to",
            ).prefetch_related("responses", "attachments"),
            id=complaint_id,
        )

        # Access rules:
        tx_org = _complaint_org_id(c)
        if org:
            # If org specified, complaint must belong to that org (only resolvable for subscription)
            if tx_org and tx_org != org.id:
                return Response({"detail": "Complaint does not belong to this organization."},
                                status=status.HTTP_403_FORBIDDEN)
            if tx_org and not OrganizationMembership.objects.filter(user=user, organization=org, is_active=True).exists():
                return Response({"detail": "You do not have access to this organization."},
                                status=status.HTTP_403_FORBIDDEN)
            # If no tx_org (purchase/general), allow creator or staff
            if tx_org is None and (c.created_by_id != user.id) and not (user.is_staff or user.is_superuser):
                return Response({"detail": "Not permitted."}, status=status.HTTP_403_FORBIDDEN)
        else:
            # No org specified: if complaint has an org (subscription), ensure membership
            if tx_org and not OrganizationMembership.objects.filter(user=user, organization_id=tx_org, is_active=True).exists():
                return Response({"detail": "You do not have access to this organization."},
                                status=status.HTTP_403_FORBIDDEN)
            # If no org (purchase/general), allow creator or staff
            if tx_org is None and (c.created_by_id != user.id) and not (user.is_staff or user.is_superuser):
                return Response({"detail": "Not permitted."}, status=status.HTTP_403_FORBIDDEN)

        payload = _complaint_dict(c)
        _absolutize_attachment_urls(request, payload)
        return Response(payload, status=status.HTTP_200_OK)

    except Exception as e:
        print("\n[ERROR] get_complaint():", str(e))
        traceback.print_exc()
        return Response({"detail": "An unexpected error occurred.", "error": str(e)},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ======================================================
# Add Response
# ======================================================
@api_view(["POST"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def add_complaint_response(request, complaint_id: str):
    try:
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return Response({"detail": "Invalid or missing session token."}, status=status.HTTP_401_UNAUTHORIZED)

        org, org_err = _resolve_org(request)  # optional
        if org_err:
            return org_err

        c = get_object_or_404(
            Complaint.objects.select_related(
                "payment__order",
                "subscription_payment__invoice__subscription__organization",
            ),
            id=complaint_id,
        )

        tx_org = _complaint_org_id(c)

        # Membership/permission checks
        if org:
            if tx_org and tx_org != org.id:
                return Response({"detail": "Complaint does not belong to this organization."},
                                status=status.HTTP_403_FORBIDDEN)
            if tx_org and not OrganizationMembership.objects.filter(user=user, organization=org, is_active=True).exists():
                return Response({"detail": "You do not have access to this organization."},
                                status=status.HTTP_403_FORBIDDEN)
            # purchase/general: allow creator or staff
            if tx_org is None and (c.created_by_id != user.id) and not (user.is_staff or user.is_superuser):
                return Response({"detail": "Not permitted."}, status=status.HTTP_403_FORBIDDEN)
            role = "support" if tx_org and _is_org_admin_or_teacher(request, org) else "user"
        else:
            # No org given
            if tx_org and not OrganizationMembership.objects.filter(user=user, organization_id=tx_org, is_active=True).exists():
                return Response({"detail": "You do not have access to this organization."},
                                status=status.HTTP_403_FORBIDDEN)
            if tx_org is None and (c.created_by_id != user.id) and not (user.is_staff or user.is_superuser):
                return Response({"detail": "Not permitted."}, status=status.HTTP_403_FORBIDDEN)
            role = "support" if (tx_org and _is_org_admin_or_teacher(request, getattr(c.subscription_payment.invoice.subscription, "organization", None))) else "user"

        message = (request.data.get("message") or "").strip()
        if not message:
            return Response({"detail": "Missing 'message'."}, status=status.HTTP_400_BAD_REQUEST)

        resp = ComplaintResponse.objects.create(
            complaint=c,
            message=message,
            author_name=user.get_full_name() or user.email,
            role=role,
        )
        c.updated_at = timezone.now()
        c.save(update_fields=["updated_at"])

        return Response(_resp_dict(resp), status=status.HTTP_201_CREATED)

    except Exception as e:
        print("\n[ERROR] add_complaint_response():", str(e))
        traceback.print_exc()
        return Response({"detail": "An unexpected error occurred.", "error": str(e)},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ======================================================
# Update Complaint
# ======================================================
@api_view(["PATCH"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def update_complaint(request, complaint_id: str):
    try:
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return Response({"detail": "Invalid or missing session token."}, status=status.HTTP_401_UNAUTHORIZED)

        org, org_err = _resolve_org(request)  # optional
        if org_err:
            return org_err

        c = get_object_or_404(
            Complaint.objects.select_related(
                "payment__order",
                "subscription_payment__invoice__subscription__organization",
            ),
            id=complaint_id,
        )

        # Permission: if the complaint has org (subscription), enforce org admin/teacher (via helper).
        # For purchase/general (no org), allow staff/superuser only.
        tx_org = _complaint_org_id(c)
        if tx_org:
            if org and tx_org != org.id:
                return Response({"detail": "Complaint does not belong to this organization."},
                                status=status.HTTP_403_FORBIDDEN)
            # Require support/admin
            subs_org = getattr(c.subscription_payment.invoice.subscription, "organization", None)
            if not _is_org_admin_or_teacher(request, subs_org):
                return Response({"detail": "Insufficient permissions."}, status=status.HTTP_403_FORBIDDEN)
        else:
            if not (user.is_staff or user.is_superuser):
                return Response({"detail": "Insufficient permissions."}, status=status.HTTP_403_FORBIDDEN)

        status_in = request.data.get("status")
        priority_in = request.data.get("priority")
        assigned_to_id = request.data.get("assigned_to_id")

        changed = False

        if status_in:
            if status_in not in Complaint.Status.values:
                return Response({"detail": "Invalid 'status'."}, status=status.HTTP_400_BAD_REQUEST)
            c.status = status_in
            changed = True

        if priority_in:
            if priority_in not in ("low", "medium", "high"):
                return Response({"detail": "Invalid 'priority'."}, status=status.HTTP_400_BAD_REQUEST)
            c.priority = priority_in
            changed = True

        if assigned_to_id is not None:
            if assigned_to_id in ("", None, False):
                c.assigned_to = None
            else:
                from django.contrib.auth import get_user_model
                User = get_user_model()
                c.assigned_to = get_object_or_404(User, id=assigned_to_id)
            changed = True

        if not changed:
            return Response({"detail": "No valid changes supplied."}, status=status.HTTP_400_BAD_REQUEST)

        c.updated_at = timezone.now()
        c.save()

        # include attachments/responses in latest shape
        c = (
            Complaint.objects.select_related(
                "payment__order",
                "subscription_payment__invoice__subscription__organization",
                "assigned_to",
            )
            .prefetch_related("responses", "attachments")
            .get(id=complaint_id)
        )
        payload = _complaint_dict(c)
        _absolutize_attachment_urls(request, payload)

        return Response(payload, status=status.HTTP_200_OK)

    except Exception as e:
        print("\n[ERROR] update_complaint():", str(e))
        traceback.print_exc()
        return Response({"detail": "An unexpected error occurred.", "error": str(e)},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ======================================================
# Upload Attachments (add later)
# ======================================================
@api_view(["POST"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
@parser_classes([MultiPartParser, FormParser])  # must be multipart for files
def add_complaint_attachments(request, complaint_id: str):
    """
    POST files as 'attachments' or 'attachments[]' (multipart/form-data)
    """
    try:
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return Response({"detail": "Invalid or missing session token."}, status=status.HTTP_401_UNAUTHORIZED)

        org, org_err = _resolve_org(request)  # optional
        if org_err:
            return org_err

        c = get_object_or_404(
            Complaint.objects.select_related(
                "subscription_payment__invoice__subscription__organization",
                "created_by",
            ),
            id=complaint_id,
        )

        # Permission: creator, staff/superuser, or member of org (for subscription-linked complaints)
        tx_org = _complaint_org_id(c)
        if org:
            if tx_org and tx_org != org.id:
                return Response({"detail": "Complaint does not belong to this organization."},
                                status=status.HTTP_403_FORBIDDEN)
            if tx_org and not OrganizationMembership.objects.filter(user=user, organization=org, is_active=True).exists():
                return Response({"detail": "You do not have access to this organization."},
                                status=status.HTTP_403_FORBIDDEN)
            if tx_org is None and (c.created_by_id != user.id) and not (user.is_staff or user.is_superuser):
                return Response({"detail": "Not permitted."}, status=status.HTTP_403_FORBIDDEN)
        else:
            if tx_org:
                if not OrganizationMembership.objects.filter(user=user, organization_id=tx_org, is_active=True).exists():
                    return Response({"detail": "You do not have access to this organization."},
                                    status=status.HTTP_403_FORBIDDEN)
            elif (c.created_by_id != user.id) and not (user.is_staff or user.is_superuser):
                return Response({"detail": "Not permitted."}, status=status.HTTP_403_FORBIDDEN)

        try:
            created = _save_attachments_from_request(request, c, user)
        except ValueError as ve:
            return Response({"detail": str(ve)}, status=status.HTTP_400_BAD_REQUEST)

        data = [_attachment_dict(a) for a in created]
        _absolutize_attachment_urls(request, data)
        # Touch updated_at
        c.updated_at = timezone.now()
        c.save(update_fields=["updated_at"])

        return Response({"attachments": data}, status=status.HTTP_201_CREATED)

    except Exception as e:
        print("\n[ERROR] add_complaint_attachments():", str(e))
        traceback.print_exc()
        return Response({"detail": "An unexpected error occurred.", "error": str(e)},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ======================================================
# Delete Attachment
# ======================================================
@api_view(["DELETE"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def delete_complaint_attachment(request, complaint_id: str, attachment_id: str):
    try:
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return Response({"detail": "Invalid or missing session token."}, status=status.HTTP_401_UNAUTHORIZED)

        org, org_err = _resolve_org(request)  # optional
        if org_err:
            return org_err

        a = get_object_or_404(
            ComplaintAttachment.objects.select_related(
                "complaint__subscription_payment__invoice__subscription__organization",
                "complaint__created_by",
            ),
            id=attachment_id,
            complaint_id=complaint_id,
        )
        c = a.complaint
        tx_org = _complaint_org_id(c)

        # Same permission shape as update_complaint: org admins/teachers can delete if sub-linked; else staff/superuser or creator
        if tx_org:
            subs_org = getattr(c.subscription_payment.invoice.subscription, "organization", None)
            if org and tx_org != org.id:
                return Response({"detail": "Complaint does not belong to this organization."},
                                status=status.HTTP_403_FORBIDDEN)
            if not _is_org_admin_or_teacher(request, subs_org):
                return Response({"detail": "Insufficient permissions."}, status=status.HTTP_403_FORBIDDEN)
        else:
            if not (user.is_staff or user.is_superuser or c.created_by_id == user.id):
                return Response({"detail": "Insufficient permissions."}, status=status.HTTP_403_FORBIDDEN)

        a.delete()
        c.updated_at = timezone.now()
        c.save(update_fields=["updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)

    except Exception as e:
        print("\n[ERROR] delete_complaint_attachment():", str(e))
        traceback.print_exc()
        return Response({"detail": "An unexpected error occurred.", "error": str(e)},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR)
