from texagonbackend.settings import PAYMENT_TEST ,TEST_KEY_SECRET,FLW_SECRET_KEY, LOGO_URL
import requests
from django.contrib.sites.shortcuts import get_current_site
from .models import SubscriptionPayment
from django.utils import timezone
from decimal import Decimal
import base64, hmac, hashlib

def get_payment_link(payload):
    if PAYMENT_TEST:
        FLW_SECRET_KEY = TEST_KEY_SECRET
        
    url = "https://api.flutterwave.com/v3/payments"

    headers = {
        "Authorization": f"Bearer {FLW_SECRET_KEY}"
    }
    #{'status': 'success', 'message': 'Hosted Link', 'data': 
    #{'link': 'https://ravemodal-dev.herokuapp.com/v3/hosted/pay/743317355dda6b531839'}}
    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        json_response = response.json()
        if json_response['status'] == 'success':
            return json_response['data']['link']
        return False
    except requests.exceptions.HTTPError as errh:
        print(f"HTTP Error: {errh}")
        return False
    except requests.exceptions.RequestException as err:
        #print(f"Request Exception: {err}")
        return False


def generate_payment_link(request, user_id:int, tx_ref:str,redirect_url:str,title:str,customer_detail:dict,total_amount:float, payment_plan:str):

    payload = {
        "tx_ref": tx_ref,
        "amount": f"{total_amount}",
        "currency": "NGN",
        "payment_options":"card",
        "redirect_url": redirect_url,
        "meta": {
            "consumer_id": f"{user_id}",
            "payment_plan":payment_plan,
        },
        "customer": customer_detail,
        "customizations": {
            "title": title,
            "logo": LOGO_URL
        }
    }

    return get_payment_link(payload)

def confirm_transaction(transaction_id: str) -> dict:
    """
    Verify a Flutterwave transaction and return a consistent payload:
    {
      "ok": bool,                 # we reached Flutterwave and parsed response
      "http_status": int|None,    # HTTP status code if available
      "status": str|None,         # flutterwave top-level "status" (e.g. "success"/"error")
      "message": str|None,        # flutterwave top-level "message"
      "data": dict|None,          # flutterwave "data" object (contains transaction status, amounts, etc.)
      "raw": dict|str|None,       # raw parsed JSON or raw text for debugging
      "error": str|None           # python/network/parse error message
    }
    """
    # Choose keys
    if PAYMENT_TEST:
        secret_key = TEST_KEY_SECRET
    else:
        secret_key = FLW_SECRET_KEY

    url = f"https://api.flutterwave.com/v3/transactions/{transaction_id}/verify"
    headers = {
        "Authorization": f"Bearer {secret_key}",
        "Content-Type": "application/json",
    }

    try:
        resp = requests.get(url, headers=headers, timeout=20)
        http_status = resp.status_code

        # Try JSON first
        try:
            payload = resp.json()
        except ValueError:
            # Non-JSON response
            return {
                "ok": False,
                "http_status": http_status,
                "status": None,
                "message": None,
                "data": None,
                "raw": resp.text,
                "error": "Non-JSON response from Flutterwave",
            }

        # If Flutterwave returns 4xx/5xx, still capture body for debugging
        if not resp.ok:
            return {
                "ok": False,
                "http_status": http_status,
                "status": (payload.get("status") or "").lower() or None,
                "message": payload.get("message"),
                "data": payload.get("data"),
                "raw": payload,
                "error": f"HTTP {http_status} from Flutterwave",
            }

        return {
            "ok": True,
            "http_status": http_status,
            "status": (payload.get("status") or "").lower() or None,   # top-level
            "message": payload.get("message"),
            "data": payload.get("data") or {},
            "raw": payload,
            "error": None,
        }

    except requests.Timeout:
        return {
            "ok": False,
            "http_status": None,
            "status": None,
            "message": None,
            "data": None,
            "raw": None,
            "error": "Timeout verifying transaction",
        }
    except requests.RequestException as e:
        return {
            "ok": False,
            "http_status": None,
            "status": None,
            "message": None,
            "data": None,
            "raw": None,
            "error": f"Network error: {str(e)}",
        }



def normalize_flutterwave_status(raw: str | None) -> str:
    s = (raw or "").strip().lower()
    if s in {"successful", "success"}:
        return SubscriptionPayment.Status.SUCCESS
    if s in {"failed", "failure"}:
        return SubscriptionPayment.Status.FAILED
    if s in {"cancelled", "canceled"}:
        return SubscriptionPayment.Status.CANCELLED
    if s in {"pending", "processing", "in_progress", "inprogress"}:
        return SubscriptionPayment.Status.INPROGRESS
    if s:
        return SubscriptionPayment.Status.UNKNOWN
    return SubscriptionPayment.Status.ERROR


def _safe_meta_patch(payment: "SubscriptionPayment", patch: dict):
    m = payment.meta or {}
    m.update(patch or {})
    payment.meta = m


def _mark_payment_status(payment: "SubscriptionPayment", *, status: str, provider_status: str | None, meta_patch: dict | None):
    """
    Avoid relying on change_current_trans() if it sets paid_at for failed/cancelled.
    We set paid_at ONLY on success.
    """
    payment.status = status
    if hasattr(payment, "provider_status"):
        payment.provider_status = provider_status
    _safe_meta_patch(payment, meta_patch or {})

    if status == SubscriptionPayment.Status.SUCCESS:
        payment.paid_at = timezone.now()

    # Always store updated_at if you have it from TimeStampedModel
    payment.save()


def _validate_verified_transaction(
    flw_data: dict,
    *,
    expected_tx_ref: str,
    expected_currency: str,
    expected_amount: Decimal,
) -> list[str]:
    tx_ref = (flw_data.get("tx_ref") or flw_data.get("reference") or "").strip()
    currency = (flw_data.get("currency") or "").strip()
    charged = flw_data.get("charged_amount") or flw_data.get("amount") or "0"

    try:
        charged_dec = Decimal(str(charged))
    except Exception:
        charged_dec = Decimal("0")

    errs = []
    if expected_tx_ref and tx_ref and tx_ref != expected_tx_ref:
        errs.append("tx_ref mismatch")
    if expected_currency and currency and currency != expected_currency:
        errs.append("currency mismatch")
    if expected_amount and charged_dec < expected_amount:
        errs.append("amount too low")

    return errs


def verify_flw_signature(raw_body: bytes, header_sig: str, secret_hash: str) -> bool:
    digest = hmac.new(secret_hash.encode(), raw_body, hashlib.sha256).digest()
    computed = base64.b64encode(digest).decode()
    return hmac.compare_digest(computed, header_sig or "")