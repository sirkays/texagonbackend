from django.urls import path
from . import views

urlpatterns = [

    # catalog
    path("categories", views.categories_list),
    path("products", views.products_list),
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
]
