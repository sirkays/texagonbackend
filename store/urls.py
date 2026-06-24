from django.urls import path
from . import views



product_reviews = views.ProductReviewViewSet.as_view({
    "get": "list",
    "post": "create",
})

product_my_review = views.ProductReviewViewSet.as_view({
    "get": "my_review",
})

urlpatterns = [

    # catalog
    path("categories/", views.categories_list),
    path("products/", views.products_list),
    path("products/<slug:slug>/", views.product_detail),

    # cart
    path("cart/", views.cart_get),
    path("cart/add/", views.cart_add),
    path("cart/items/<uuid:item_id>/", views.cart_update_item),
    path("cart/items/<uuid:item_id>/remove/", views.cart_remove_item),
    path("cart/apply-coupon/", views.cart_apply_coupon),

    # addresses
    path("addresses/", views.address_list_create),
    path("addresses/<uuid:address_id>/", views.address_update_delete),

    # checkout/orders/payments
    path("checkout/create-order/", views.checkout_create_order),
    path("orders/", views.orders_list),
    path("orders/<uuid:order_id>/", views.order_detail),
    
    # BNPL
    path("bnpl/plans/", views.bnpl_plans),
    path("bnpl/<uuid:order_id>/start/", views.bnpl_start),
    path("bnpl/agreements/<uuid:agreement_id>/", views.bnpl_agreement_detail),
    path("bnpl/breakdown/", views.bnpl_breakdown, name="bnpl-breakdown"),


    # returns
    path("orders/<uuid:order_id>/rma/", views.rma_create),


    # Customer-facing
    path("orders/<uuid:order_id>/shipments/", views.order_shipments_list, name="order-shipments-list"),
    path("shipments/<uuid:shipment_id>/", views.shipment_detail, name="shipment-detail"),
    path("shipments/track/", views.track_by_number, name="shipment-track-by-number"),

    # Staff/Ops
    path("orders/<uuid:order_id>/shipments/create/", views.shipment_create, name="shipment-create"),
    path("shipments/<uuid:shipment_id>/set-tracking/", views.shipment_set_tracking, name="shipment-set-tracking"),
    path("shipments/<uuid:shipment_id>/status/", views.shipment_update_status, name="shipment-update-status"),
    path("shipments/<uuid:shipment_id>/events/", views.shipment_add_event, name="shipment-add-event"),
    path("shipping/options/", views.shipping_options),
    path("list/shipments/", views.list_shipments, name="store-shipments-list"),

    # Webhook (carrier updates)
    path("webhooks/tracking/", views.tracking_webhook, name="tracking-webhook"),


    path("products/<slug:slug>/reviews/", product_reviews, name="product-reviews"),
    path("products/<slug:slug>/reviews/my-review/", product_my_review, name="product-my-review")

]
