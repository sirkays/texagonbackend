from django.urls import path
from . import views

urlpatterns = [

    # catalog
    path("categories/", views.categories_list),
    path("products/", views.products_list),
    path("products/<slug:slug>", views.product_detail),

    # cart
    path("cart", views.cart_get),
    path("cart/add", views.cart_add),
    path("cart/items/<uuid:item_id>", views.cart_update_item),
    path("cart/items/<uuid:item_id>/remove", views.cart_remove_item),
    path("cart/apply-coupon", views.cart_apply_coupon),

    # addresses
    path("addresses", views.address_list_create),
    path("addresses/<uuid:address_id>", views.address_update_delete),

    # checkout/orders/payments
    path("checkout/create-order", views.checkout_create_order),
    path("orders", views.orders_list),
    path("orders/<uuid:order_id>", views.order_detail),

    path("payments/card/<uuid:order_id>/start", views.payment_card_start),
    path("payments/<uuid:payment_id>/mark-captured", views.payment_mark_captured),

    # BNPL
    path("bnpl/plans", views.bnpl_plans),
    path("bnpl/<uuid:order_id>/start", views.bnpl_start),
    path("bnpl/agreements/<uuid:agreement_id>", views.bnpl_agreement_detail),

    # reviews
    path("reviews/<uuid:product_id>", views.review_create),

    # returns
    path("orders/<uuid:order_id>/rma", views.rma_create),

    # digital entitlements
    path("me/entitlements", views.entitlements_list),

    # Customer-facing
    path("orders/<uuid:order_id>/shipments/", views.order_shipments_list, name="order-shipments-list"),
    path("shipments/<uuid:shipment_id>/", views.shipment_detail, name="shipment-detail"),
    path("shipments/track/", views.track_by_number, name="shipment-track-by-number"),

    # Staff/Ops
    path("orders/<uuid:order_id>/shipments/create/", views.shipment_create, name="shipment-create"),
    path("shipments/<uuid:shipment_id>/set-tracking/", views.shipment_set_tracking, name="shipment-set-tracking"),
    path("shipments/<uuid:shipment_id>/status/", views.shipment_update_status, name="shipment-update-status"),
    path("shipments/<uuid:shipment_id>/events/", views.shipment_add_event, name="shipment-add-event"),

    # Webhook (carrier updates)
    path("webhooks/tracking/", views.tracking_webhook, name="tracking-webhook"),
]
