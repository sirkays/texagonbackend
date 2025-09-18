# billing/api/payments.py  (example path — adjust to your project)
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.shortcuts import get_object_or_404

from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.response import Response
from rest_framework import status

from rest_framework_api_key.permissions import HasAPIKey
from api.authentication import SessionTokenAuthentication  # your auth
from orgs.models import OrganizationMembership
from billing.models import SubscriptionInvoice, SubscriptionPayment  # adjust paths
from .utils import generate_payment_link,confirm_transaction
import secrets
from core.utils import get_object_or_404_ajax
from academics.models import ParentProfile  # adjust import path to your ParentProfile


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
def create_subscription_payment(request):
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

    if not redirect_url:
        return Response({"detail": "Missing 'redirect_url' in request body."}, status=status.HTTP_400_BAD_REQUEST)


    if not invoice_id:
        return Response({"detail": "Missing 'invoice' id in request body."}, status=status.HTTP_400_BAD_REQUEST)

    # load invoice with subscription -> organization
    try:
        invoice = SubscriptionInvoice.objects.select_related("subscription__organization").get(number=invoice_id)
    except SubscriptionInvoice.DoesNotExist:
        return Response({"detail": "Invoice not found."}, status=status.HTTP_404_NOT_FOUND)

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
        method="",  # left blank per your spec
        status=SubscriptionPayment.Status.CREATED,
        # paid_at defaults to timezone.now() per your model; we can leave it as default
        meta={"created_by_membership_id": membership.pk, "created_by_user_id": user.pk},
    )

    try:
        payment.save()
        payment_plan = invoice.subscription.plan.name
        customer_detail = {
            "email":request.user.email,
        }
        title = "Subscription Payment"
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
    GET /api/parent/invoices/?status=paid

    Returns invoices for the logged-in parent. Filters by:
      - invoices where organization_membership == the parent's membership (preferred)
      - fallback: invoices whose meta references the parent_profile_id (compatibility)
    Optional: ?status=<one_of_open,paid,void,uncollectible,active>
    """
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

    status_param = request.query_params.get("status")
    if status_param:
        valid_statuses = {choice[0] for choice in SubscriptionInvoice.Status.choices}
        if status_param not in valid_statuses:
            return Response(
                {"detail": f"Invalid status '{status_param}'. Valid: {', '.join(sorted(valid_statuses))}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

    qs = SubscriptionInvoice.objects.select_related("subscription__organization", "organization_membership")

    if membership:
        # Preferred branch: filter by FK
        invoices_qs = qs.filter(organization_membership=membership).order_by("-issued_at")
    else:
        return Response({"detail": "Membership profile not found for this user."}, status=status.HTTP_404_NOT_FOUND)
    if status_param:
        invoices_qs = invoices_qs.filter(status=status_param)
    # Simple serializer
    def _serialize(inv: SubscriptionInvoice):
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
            "meta": inv.meta or {},
            "created_at": getattr(inv, "created_at", None).isoformat() if getattr(inv, "created_at", None) else None,
            "updated_at": getattr(inv, "updated_at", None).isoformat() if getattr(inv, "updated_at", None) else None,
        }

    data = [_serialize(inv) for inv in invoices_qs]
    return Response({"count": len(data), "results": data}, status=status.HTTP_200_OK)


@api_view(["POST"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def confirm_payement(request):
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
        invoice__number=invoice_id, invoice__organization_membership__user=request.user,
    )

    if subscription_payment is False:
        return Response({"detail": "We could not confirm payment at this moment. Contact support if it not confirmed in 30min"}, 
        status=status.HTTP_404_NOT_FOUND)
    return Response({"status":"success"}, status=status.HTTP_200_OK)
    res = confirm_transaction(transaction_id)
    
    if res[1] != "success":
        messages.warning(request, 
        f'Something went wrong, Contact support at info@texagon.epichouse.online with transaction no. {transaction_id}')
        subscription_payment.change_current_trans("failed")
        return Response({"detail": "Could not update payment, because we could not confirm payment"}, status=status.HTTP_400_BAD_REQUEST)

    subscription_payment.change_current_trans("success")

    subscription_payment.invoice.status = "paid"
    subscription_payment.invoice.save()
    
    return Response({"status":"success"}, status=status.HTTP_200_OK)
















