# store/admin.py
from __future__ import annotations

from decimal import Decimal
from django.contrib import admin, messages
from django.db.models import Count, Sum
from django.utils import timezone

from .models import (
    # Catalog
    Category, Product, ProductImage, Review, Coupon,
    # Cart / Orders / Payments / Entitlements
    Cart, CartItem, Address,
    Order, OrderItem, Payment, Entitlement,
    # BNPL
    BNPLPlanTemplate, BNPLAgreement, BNPLInstallment,
    # Shipping / Tracking
    ShippingCarrier, ShippingMethod, Shipment, ShipmentItem, TrackingEvent,
    # Returns (RMA)
    ReturnAuthorization, ReturnItem,
)

# -------------------------
# Inlines
# -------------------------

class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 0
    fields = ("product_image", "alt_text", "sort_order")
    ordering = ("sort_order",)

class ReviewInline(admin.TabularInline):
    model = Review
    extra = 0
    fields = ("user", "rating", "title", "body", "created_at")
    readonly_fields = ("created_at",)
    autocomplete_fields = ("user",)

class CartItemInline(admin.TabularInline):
    model = CartItem
    extra = 0
    autocomplete_fields = ("product",)

class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ("title_snapshot", "unit_price", "line_total", "created_at", "updated_at")
    fields = ("product", "title_snapshot", "unit_price", "quantity", "line_total", "created_at")
    autocomplete_fields = ("product",)

class PaymentInline(admin.StackedInline):
    model = Payment
    extra = 0
    can_delete = False
    readonly_fields = ("created_at", "updated_at")

class BNPLInstallmentInline(admin.TabularInline):
    model = BNPLInstallment
    extra = 0
    readonly_fields = ("created_at", "updated_at")
    fields = ("index", "due_at", "amount_due", "amount_paid", "status", "capture_immediately", "provider_charge_id", "created_at")
    ordering = ("index",)

class ShipmentItemInline(admin.TabularInline):
    model = ShipmentItem
    extra = 0
    autocomplete_fields = ("order_item",)

class TrackingEventInline(admin.TabularInline):
    model = TrackingEvent
    extra = 0
    fields = ("event_code", "description", "occurred_at", "city", "state", "country", "postal_code", "carrier_status")
    ordering = ("occurred_at",)

class ReturnItemInline(admin.TabularInline):
    model = ReturnItem
    extra = 0
    autocomplete_fields = ("order_item",)
    fields = ("order_item", "quantity", "condition", "reason", "approved", "refund_amount")


# -------------------------
# Catalog
# -------------------------

@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    autocomplete_fields = ['parent']
    list_display = ("name", "slug", "parent")
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}
    list_filter = ("parent",)
    ordering = ("name",)


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("title", "product_type", "category", "price", "rating", "rating_count", "is_digital", "stock", "is_active", "created_at")
    list_filter = ("product_type", "category", "is_active", "is_digital")
    search_fields = ("title", "slug", "description", "sku")
    list_select_related = ("category",)
    inlines = [ProductImageInline, ReviewInline]
    readonly_fields = ("created_at", "updated_at", "pay_in_4_amount")
    autocomplete_fields = ("category", "default_bnpl_plan") if hasattr(Product, "default_bnpl_plan") else ("category",)
    fieldsets = (
        ("Basics", {"fields": ("title", "slug", "product_type", "category", "description", "is_active")}),
        ("Pricing", {"fields": ("price", "pay_in_4_amount")}),
        ("Ratings", {"fields": ("rating", "rating_count")}),
        ("Fulfillment", {"fields": ("is_digital", "sku", "stock")}),
        ("BNPL", {"fields": tuple(
            f for f in ("bnpl_enabled", "default_bnpl_plan") if hasattr(Product, f)
        ) or ()}),
        ("Meta", {"fields": ("created_at", "updated_at")}),
    )

    @admin.action(description="Recalculate pay-in-4 amount")
    def recalc_pay_in_4(self, request, queryset):
        updated = 0
        for p in queryset:
            p.pay_in_4_amount = (p.price / Decimal("4")).quantize(Decimal("0.01"))
            p.save(update_fields=["pay_in_4_amount"])
            updated += 1
        self.message_user(request, f"Updated {updated} products.", level=messages.SUCCESS)

    actions = ["recalc_pay_in_4"]


@admin.register(Coupon)
class CouponAdmin(admin.ModelAdmin):
    list_display = ("code", "discount_type", "value", "active", "starts_at", "ends_at", "usage_limit", "used_count")
    list_filter = ("discount_type", "active")
    search_fields = ("code",)
    readonly_fields = ("used_count",)


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ("product", "user", "rating", "title", "created_at")
    list_filter = ("rating", "created_at")
    search_fields = ("title", "body", "product__title", "user__email")
    autocomplete_fields = ("product", "user")
    ordering = ("-created_at",)


# -------------------------
# Cart / Addresses
# -------------------------

@admin.register(Cart)
class CartAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "session_key", "active", "coupon", "created_at")
    list_filter = ("active",)
    search_fields = ("session_key", "user__email")
    autocomplete_fields = ("user", "coupon")
    inlines = [CartItemInline]
    readonly_fields = ("created_at", "updated_at")


@admin.register(Address)
class AddressAdmin(admin.ModelAdmin):
    list_display = ("user", "full_name", "line1", "city", "state", "postal_code", "country", "is_default")
    list_filter = ("country", "is_default")
    search_fields = ("full_name", "line1", "city", "postal_code", "user__email")
    autocomplete_fields = ("user",)


# -------------------------
# Orders / Payments / Entitlements
# -------------------------

@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "status", "grand_total", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("id", "user__email", "coupon_code")
    autocomplete_fields = ("user", "billing_address", "shipping_address")
    inlines = [OrderItemInline, PaymentInline]
    readonly_fields = ("subtotal", "discount_total", "tax_total", "shipping_total", "grand_total", "coupon_code", "created_at", "updated_at")

    @admin.action(description="Mark as fulfilled")
    def mark_fulfilled(self, request, queryset):
        count = queryset.update(status=Order.Status.FULFILLED)
        self.message_user(request, f"Marked {count} orders as fulfilled.", level=messages.SUCCESS)

    actions = ["mark_fulfilled"]

@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = ("order", "product", "title_snapshot", "quantity", "unit_price", "line_total", "created_at")
    list_select_related = ("order", "product")
    search_fields = (
        "title_snapshot",
        "order__id",
        "order__user__email",
        "product__title",
        "product__sku",
    )
    autocomplete_fields = ("order", "product")
    readonly_fields = ("created_at", "updated_at")
    ordering = ("-created_at",)


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("id", "order", "provider", "status", "amount", "currency", "provider_ref", "created_at")
    list_filter = ("provider", "status", "currency")
    search_fields = ("id", "provider_ref", "order__id", "order__user__email")
    autocomplete_fields = ("order",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(Entitlement)
class EntitlementAdmin(admin.ModelAdmin):
    list_display = ("user", "product", "created_at")
    search_fields = ("user__email", "product__title")
    list_select_related = ("user", "product")
    autocomplete_fields = ("user", "product")
    readonly_fields = ("created_at", "updated_at")


# -------------------------
# BNPL
# -------------------------

@admin.register(BNPLPlanTemplate)
class BNPLPlanTemplateAdmin(admin.ModelAdmin):
    list_display = ("provider", "name", "num_installments", "interval_days", "currency", "min_amount", "max_amount", "active")
    list_filter = ("provider", "currency", "active")
    search_fields = ("name", "provider")
    ordering = ("provider", "name")


@admin.register(BNPLAgreement)
class BNPLAgreementAdmin(admin.ModelAdmin):
    list_display = ("id", "order", "provider", "status", "total_amount", "amount_paid", "amount_outstanding", "created_at")
    list_filter = ("provider", "status", "created_at")
    search_fields = ("id", "order__id", "provider_checkout_id", "provider_agreement_id", "order__user__email")
    autocomplete_fields = ("order", "plan")
    inlines = [BNPLInstallmentInline]
    readonly_fields = ("principal_amount", "customer_fee_flat", "customer_fee_rate", "total_amount", "amount_paid", "amount_outstanding", "provider_checkout_id", "provider_agreement_id", "created_at", "updated_at")

    @admin.action(description="Force complete (if zero outstanding)")
    def force_complete(self, request, queryset):
        done = 0
        for ag in queryset:
            if ag.amount_outstanding <= Decimal("0.00"):
                ag.status = BNPLAgreement.Status.COMPLETED
                ag.save(update_fields=["status"])
                done += 1
        self.message_user(request, f"Completed {done} agreements.", level=messages.SUCCESS)

    actions = ["force_complete"]


@admin.register(BNPLInstallment)
class BNPLInstallmentAdmin(admin.ModelAdmin):
    list_display = ("agreement", "index", "due_at", "amount_due", "amount_paid", "status", "capture_immediately")
    list_filter = ("status", "capture_immediately")
    search_fields = ("agreement__id",)
    autocomplete_fields = ("agreement",)
    ordering = ("agreement", "index")


# -------------------------
# Shipping / Tracking
# -------------------------

@admin.register(ShippingCarrier)
class ShippingCarrierAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "api_slug", "active")
    list_filter = ("active",)
    search_fields = ("code", "name", "api_slug")


@admin.register(ShippingMethod)
class ShippingMethodAdmin(admin.ModelAdmin):
    list_display = ("carrier", "name", "service_code", "est_min_days", "est_max_days", "active")
    list_filter = ("carrier", "active")
    search_fields = ("name", "service_code")
    autocomplete_fields = ("carrier",)


@admin.register(Shipment)
class ShipmentAdmin(admin.ModelAdmin):
    list_display = ("id", "order", "status", "carrier", "tracking_number", "shipped_at", "delivered_at", "to_country")
    list_filter = ("status", "carrier", "to_country")
    search_fields = ("id", "order__id", "tracking_number", "to_name", "to_postal_code")
    list_select_related = ("order", "carrier", "method")
    inlines = [ShipmentItemInline, TrackingEventInline]
    readonly_fields = ("created_at", "updated_at")
    autocomplete_fields = ("order", "carrier", "method")

    @admin.action(description="Mark delivered (now)")
    def mark_delivered(self, request, queryset):
        now = timezone.now()
        count = 0
        for s in queryset:
            s.status = Shipment.Status.DELIVERED
            s.delivered_at = now
            s.save(update_fields=["status", "delivered_at"])
            count += 1
        self.message_user(request, f"Marked {count} shipments delivered.", level=messages.SUCCESS)

    actions = ["mark_delivered"]


@admin.register(TrackingEvent)
class TrackingEventAdmin(admin.ModelAdmin):
    list_display = ("shipment", "event_code", "occurred_at", "city", "state", "country", "carrier_status")
    list_filter = ("event_code", "country")
    search_fields = ("shipment__tracking_number", "description", "city", "state", "postal_code")
    autocomplete_fields = ("shipment",)
    ordering = ("-occurred_at",)


# -------------------------
# Returns (RMA)
# -------------------------

@admin.register(ReturnAuthorization)
class ReturnAuthorizationAdmin(admin.ModelAdmin):
    list_display = ("rma_number", "order", "status", "refund_total", "carrier", "tracking_number", "created_at")
    list_filter = ("status", "carrier")
    search_fields = ("rma_number", "order__id", "tracking_number", "order__user__email")
    autocomplete_fields = ("order", "carrier")
    inlines = [ReturnItemInline]
    readonly_fields = ("created_at", "updated_at")

    @admin.action(description="Mark approved")
    def mark_approved(self, request, queryset):
        count = queryset.update(status=ReturnAuthorization.Status.APPROVED)
        self.message_user(request, f"Approved {count} RMAs.", level=messages.SUCCESS)

    @admin.action(description="Mark refunded")
    def mark_refunded(self, request, queryset):
        count = queryset.update(status=ReturnAuthorization.Status.REFUNDED)
        self.message_user(request, f"Refunded {count} RMAs.", level=messages.SUCCESS)

    actions = ["mark_approved", "mark_refunded"]


@admin.register(ReturnItem)
class ReturnItemAdmin(admin.ModelAdmin):
    list_display = ("rma", "order_item", "quantity", "approved", "refund_amount", "created_at")
    list_filter = ("approved",)
    search_fields = ("rma__rma_number", "order_item__title_snapshot", "order_item__order__id")
    autocomplete_fields = ("rma", "order_item")


# -------------------------
# Misc tweaks
# -------------------------

def _totals_report(modeladmin, request, queryset):
    """Tiny helper to show quick sums in the admin messages (callable for actions if desired)."""
    agg = queryset.aggregate(
        orders=Count("id"),
        total=Sum("grand_total") if queryset.model is Order else None
    )
    model_name = queryset.model._meta.verbose_name_plural.title()
    total = agg.get("total")
    msg = f"{model_name} selected: {agg.get('orders')}."
    if total is not None:
        msg += f" Sum(grand_total)={total}"
    modeladmin.message_user(request, msg, level=messages.INFO)
