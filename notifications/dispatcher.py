#texagon_academy\texagonbackend\notifications\dispatcher.py
from django.db import transaction
from texagonbackend.settings import FRONTEND_ORIGIN, SITE_NAME
from notifications.services import dispatch
from notifications.events import ORDER_CREATED


def _dispatch_order_created(user, order, items, *, is_bnpl=False, is_buy_now=False, bnpl=None):
    order_url = f"{FRONTEND_ORIGIN}/store?tab=orders"  # adjust to your FE route

    data = {
        "order_id": str(order.id),
        "grand_total": str(order.grand_total),
        "currency": "NGN",
        "is_bnpl": bool(is_bnpl),
        "is_buy_now": bool(is_buy_now),
        "items": items,
        "cta": {"label": "View order", "url": order_url},
    }

    if bnpl:
        data.update({
            "bnpl_pay_today": str(bnpl.get("pay_today", "")),
            "bnpl_total_amount": str(bnpl.get("total_amount", "")),
            "bnpl_num_installments": int(bnpl.get("num_installments", 0) or 0),
            "bnpl_interval_days": int(bnpl.get("interval_days", 0) or 0),
        })

    dispatch(
        users=[user],
        message=ORDER_CREATED,
        ctx={"app_name": SITE_NAME},
        data=data,
        send_in_app=True,
        send_email=True,
        fail_silently=True,
    )

