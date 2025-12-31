# store/models.py
from __future__ import annotations

import uuid
from decimal import Decimal
from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _
from core.models import TimeStampedModel
from datetime import timedelta
from django.utils import timezone
from django.contrib.postgres.fields import ArrayField  # if not on Postgres, replace or remove


class Category(TimeStampedModel):
    """
    Simple hierarchical categories for the filter chips (e.g., 'Online Courses', 'Books & eBooks').
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(max_length=140, unique=True)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="children")

    class Meta:
        verbose_name_plural = "categories"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Product(TimeStampedModel):
    class ProductType(models.TextChoices):
        COURSE = "course", _("Online Course")
        BOOK = "book", _("Book / eBook")
        AUDIO = "audio", _("Audio Course")
        HARDWARE = "hardware", _("Hardware")
        BUNDLE = "bundle", _("Bundle")
        BOOTCAMP = "bootcamp", _("Bootcamp")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=200, db_index=True)
    slug = models.SlugField(max_length=220, unique=True, default="slug001")
    product_type = models.CharField(max_length=20, choices=ProductType.choices, db_index=True, default=ProductType.HARDWARE)

    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="products", blank=True, null=True)
    description = models.TextField(blank=True)

    # Pricing
    price = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal("0.00"))])
    # Denormalized “pay in 4” display (optional). If null, compute on the fly.
    pay_in_4_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    bnpl_enabled = models.BooleanField(default=True)
    default_bnpl_plan = models.ForeignKey(
        "BNPLPlanTemplate", null=True, blank=True, on_delete=models.SET_NULL, related_name="default_for_products"
    )

    # Ratings shown on the tiles (e.g., 4.8 (2847))
    rating = models.DecimalField(
        max_digits=3, decimal_places=1, default=Decimal("0.0"),
        validators=[MinValueValidator(Decimal("0.0")), MaxValueValidator(Decimal("5.0"))],
        help_text="Average rating 0.0–5.0"
    )
    rating_count = models.PositiveIntegerField(default=0)

    # Digital/physical flags
    is_digital = models.BooleanField(default=True, help_text="False for hardware/physical items.")
    sku = models.CharField(max_length=64, blank=True, help_text="Required/used for physical items.")
    stock = models.PositiveIntegerField(default=0, help_text="Inventory for physical items only.")

    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["title"]
        indexes = [
            models.Index(fields=["product_type", "is_active"]),
            models.Index(fields=["price"]),
        ]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if self.pay_in_4_amount is None and self.price is not None:
            # round to 2dp for display parity
            self.pay_in_4_amount = (self.price / Decimal("4")).quantize(Decimal("0.01"))
        super().save(*args, **kwargs)

class ProductImage(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="images")
    product_image = models.ImageField(upload_to="texagon/product_images/", blank=True, null=True)  # use ImageField
    alt_text = models.CharField(max_length=200, blank=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "created_at"]

    def get_absolute_url(self, request=None):
        if self.product_image:
            if request:
                return request.build_absolute_uri(self.product_image.url)
            return f"{settings.MEDIA_URL}{self.product_image.name}"
        return ""


class Coupon(TimeStampedModel):
    PERCENT = "percent"
    FIXED = "fixed"
    DISCOUNT_TYPE_CHOICES = [(PERCENT, "Percent"), (FIXED, "Fixed amount")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=40, unique=True)
    discount_type = models.CharField(max_length=10, choices=DISCOUNT_TYPE_CHOICES)
    value = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal("0.00"))])
    active = models.BooleanField(default=True)
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    usage_limit = models.PositiveIntegerField(null=True, blank=True)
    used_count = models.PositiveIntegerField(default=0)

    def __str__(self):
        return self.code


class Review(TimeStampedModel):
    """
    Individual reviews; keep Product.rating and rating_count denormalized for fast listing.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="reviews")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="product_reviews")
    rating = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    title = models.CharField(max_length=150, blank=True)
    body = models.TextField(blank=True)

    class Meta:
        unique_together = [("product", "user")]  # one review per user per product
        ordering = ["-created_at"]


class Cart(TimeStampedModel):
    """
    Cart is kept even for anonymous visitors via session_key; attach user when they log in.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="carts")
    session_key = models.CharField(max_length=100, blank=True, db_index=True)
    active = models.BooleanField(default=True)
    coupon = models.ForeignKey(Coupon, null=True, blank=True, on_delete=models.SET_NULL)

    def __str__(self):
        return f"Cart {self.pk}"


class CartItem(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])

    class Meta:
        unique_together = [("cart", "product")]


class Address(TimeStampedModel):
    """
    Optional; used for physical goods checkout and receipts.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="addresses")
    full_name = models.CharField(max_length=120)
    line1 = models.CharField(max_length=200)
    line2 = models.CharField(max_length=200, blank=True)
    city = models.CharField(max_length=120)
    state = models.CharField(max_length=120, blank=True)
    postal_code = models.CharField(max_length=30, blank=True)
    country = models.CharField(max_length=2, help_text="ISO 3166-1 alpha-2")
    phone = models.CharField(max_length=40, blank=True)
    is_default = models.BooleanField(default=False)

    class Meta:
        ordering = ["-is_default", "full_name"]


class Order(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        PAID = "paid", _("Paid")
        FULFILLED = "fulfilled", _("Fulfilled")
        CANCELLED = "cancelled", _("Cancelled")
        REFUNDED = "refunded", _("Refunded")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="orders")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True)
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    discount_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    tax_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    shipping_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    grand_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    coupon_code = models.CharField(max_length=40, blank=True)
    notes = models.TextField(blank=True)

    billing_address = models.ForeignKey(Address, null=True, blank=True, on_delete=models.SET_NULL, related_name="billing_orders")
    shipping_address = models.ForeignKey(Address, null=True, blank=True, on_delete=models.SET_NULL, related_name="shipping_orders")

    def __str__(self):
        return f"Order {self.pk} — {self.get_status_display()}"

    def reduce_stock(self):
        order_items = self.items.all()
        for order_item in order_items:
            product = order_item.product
            if product.stock >= order_item.quantity:
                product.stock = product.stock - order_item.quantity
                product.save()


class OrderItem(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    title_snapshot = models.CharField(max_length=200, blank=True, null=True)  # freeze display title at the time of purchase
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    quantity = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    line_total = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True)

    def __str__(self):
        return f"{self.title_snapshot} × {self.quantity}"


class Payment(TimeStampedModel):
    class Provider(models.TextChoices):
        FLUTTERWAVE = "flutterwave", _("Flutterwave")
        PAYSTACK = "paystack", _("Paystack")

    class Status(models.TextChoices):
        INITIATED = "initiated", _("Initiated")
        AUTHORIZED = "authorized", _("Authorized")
        CAPTURED = "captured", _("Captured")
        FAILED = "failed", _("Failed")
        REFUNDED = "refunded", _("Refunded")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.OneToOneField(Order, on_delete=models.CASCADE, related_name="payment")
    provider = models.CharField(max_length=20, choices=Provider.choices, default=Provider.FLUTTERWAVE)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.INITIATED)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=10, default="NGN")
    provider_ref = models.CharField(max_length=120, blank=True)  # e.g., payment intent ID
    error_message = models.TextField(blank=True)


class Entitlement(TimeStampedModel):
    """
    Grants access for digital items after purchase (courses, ebooks, audio).
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="entitlements")
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="entitlements")

    class Meta:
        unique_together = [("user", "product")]
        indexes = [models.Index(fields=["user", "product"])]



class BNPLPlanTemplate(TimeStampedModel):
    """
    Merchant-configured 'Pay in X' template for a BNPL provider.
    Used to:
      1) show messaging on PDP/PLP,
      2) generate schedules for orders that opt into BNPL.
    """
    class Provider(models.TextChoices):
        AFTERPAY = "afterpay", _("Afterpay / Clearpay")
        AFFIRM = "affirm", _("Affirm")
        KLARNA = "klarna", _("Klarna")
        PAYPAL_PAY_IN_4 = "paypal_pi4", _("PayPal Pay in 4")
        ZIP = "zip", _("Zip")
        SEZZLE = "sezzle", _("Sezzle")
        MOCK = "mock", _("Mock")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    provider = models.CharField(max_length=20, choices=Provider.choices, db_index=True)
    name = models.CharField(max_length=120, default="Pay in 4")
    num_installments = models.PositiveSmallIntegerField(default=4, validators=[MinValueValidator(2)])
    # e.g., 14 days between installments (many providers are biweekly)
    interval_days = models.PositiveSmallIntegerField(default=14)
    # Optional downpayment as first installment (common is 25% at checkout for 4-in-4)
    take_downpayment_now = models.BooleanField(default=True)

    # Eligibility & limits
    currency = models.CharField(max_length=10, default="NGN")
    min_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    max_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    # Fees (snapshot into agreement at checkout)
    customer_fee_flat = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    customer_fee_rate = models.DecimalField(  # percent in decimal form, e.g. 0.00–0.20
        max_digits=5, decimal_places=4, default=Decimal("0.0000"),
        help_text="Applied to order total for customer (rare; many providers charge merchant instead)."
    )
    merchant_fee_rate = models.DecimalField(max_digits=5, decimal_places=4, default=Decimal("0.0000"))

    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["provider", "name"]
        unique_together = [("provider", "name"), ("provider", "num_installments", "interval_days")]

    def __str__(self):
        return f"{self.get_provider_display()} — {self.name}"


class BNPLAgreement(TimeStampedModel):
    """
    A BNPL contract tied to a single Order.
    We snapshot key plan details, totals, and provider references at checkout.
    """
    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")          # created but not confirmed by provider
        ACTIVE = "active", _("Active")             # schedule created; collecting installments
        COMPLETED = "completed", _("Completed")    # all installments settled
        FAILED = "failed", _("Failed")             # unrecoverable failure
        CANCELLED = "cancelled", _("Cancelled")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.OneToOneField("Order", on_delete=models.CASCADE, related_name="bnpl_agreement")
    plan = models.ForeignKey(BNPLPlanTemplate, on_delete=models.PROTECT, related_name="agreements")
    provider = models.CharField(max_length=20, choices=BNPLPlanTemplate.Provider.choices)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True)

    # Snapshot of plan terms used at the moment of checkout
    num_installments = models.PositiveSmallIntegerField()
    interval_days = models.PositiveSmallIntegerField()
    take_downpayment_now = models.BooleanField(default=True)

    currency = models.CharField(max_length=10, default="NGN")
    principal_amount = models.DecimalField(max_digits=12, decimal_places=2)  # based on order.grand_total
    customer_fee_flat = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    customer_fee_rate = models.DecimalField(max_digits=5, decimal_places=4, default=Decimal("0.0000"))
    total_amount = models.DecimalField(max_digits=12, decimal_places=2)  # principal + customer fees

    # Provider references
    provider_checkout_id = models.CharField(max_length=120, blank=True)
    provider_agreement_id = models.CharField(max_length=120, blank=True)

    # Convenience aggregates
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    amount_outstanding = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    class Meta:
        indexes = [
            models.Index(fields=["provider", "status"]),
        ]

    def __str__(self):
        return f"BNPL {self.get_provider_display()} for Order {self.order_id} — {self.get_status_display()}"

    def initialize_schedule(self, first_charge_at: timezone.datetime | None = None):
        """
        Create BNPLInstallment rows based on the snapshot terms.
        Call this once after the provider approves the agreement.
        """
        assert self.pk, "Save agreement before initializing schedule."
        BNPLInstallment.objects.filter(agreement=self).delete()

        per_inst = (self.total_amount / Decimal(self.num_installments)).quantize(Decimal("0.01"))
        # Adjust last installment to fix rounding pennies
        running_total = Decimal("0.00")
        now = timezone.now()
        first_due = first_charge_at or now

        for i in range(1, self.num_installments + 1):
            due = first_due if i == 1 else first_due + timedelta(days=self.interval_days * (i - 1))
            amount = per_inst
            if i == self.num_installments:
                amount = (self.total_amount - running_total).quantize(Decimal("0.01"))
            BNPLInstallment.objects.create(
                agreement=self,
                index=i,
                due_at=due,
                amount_due=amount,
                capture_immediately=(i == 1 and self.take_downpayment_now),
            )
            running_total += amount

        # initialize aggregates
        self.amount_outstanding = self.total_amount
        self.save(update_fields=["amount_outstanding"])


class BNPLInstallment(TimeStampedModel):
    """
    One scheduled installment within a BNPLAgreement.
    """
    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        AUTHORIZED = "authorized", _("Authorized")
        CAPTURED = "captured", _("Captured")
        FAILED = "failed", _("Failed")
        REFUNDED = "refunded", _("Refunded")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agreement = models.ForeignKey(BNPLAgreement, on_delete=models.CASCADE, related_name="installments")
    index = models.PositiveSmallIntegerField(help_text="1-based sequence number")
    due_at = models.DateTimeField(db_index=True)
    amount_due = models.DecimalField(max_digits=12, decimal_places=2)
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True)
    capture_immediately = models.BooleanField(default=False)

    # Link to underlying payment attempt in your provider (e.g., Stripe PI, Klarna charge id)
    provider_charge_id = models.CharField(max_length=120, blank=True)
    error_message = models.TextField(blank=True)

    class Meta:
        unique_together = [("agreement", "index")]
        ordering = ["agreement", "index"]

    @property
    def is_settled(self) -> bool:
        return self.status in {self.Status.CAPTURED, self.Status.REFUNDED}

    def mark_captured(self, amount: Decimal):
        self.amount_paid = (self.amount_paid + amount).quantize(Decimal("0.01"))
        if self.amount_paid >= self.amount_due:
            self.status = self.Status.CAPTURED
        self.save(update_fields=["amount_paid", "status"])

        # Update aggregates on the agreement
        agg = self.agreement
        agg.amount_paid = (agg.amount_paid + amount).quantize(Decimal("0.01"))
        agg.amount_outstanding = (agg.total_amount - agg.amount_paid).quantize(Decimal("0.01"))
        if agg.amount_outstanding <= Decimal("0.00"):
            agg.status = BNPLAgreement.Status.COMPLETED
        elif agg.status == BNPLAgreement.Status.PENDING:
            agg.status = BNPLAgreement.Status.ACTIVE
        agg.save(update_fields=["amount_paid", "amount_outstanding", "status"])



class ShippingCarrier(models.Model):
    """
    Known carriers & API identifiers. Keep it small and editable in admin.
    """
    class Code(models.TextChoices):
        UPS = "ups", _("UPS")
        USPS = "usps", _("USPS")
        FEDEX = "fedex", _("FedEx")
        DHL = "dhl", _("DHL")
        ROYALMAIL = "royal_mail", _("Royal Mail")
        DPD = "dpd", _("DPD")
        LOCAL = "local_courier", _("Local Courier")
        OTHER = "other", _("Other")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=40, choices=Code.choices, db_index=True)
    name = models.CharField(max_length=120)
    api_slug = models.CharField(max_length=120, blank=True, help_text="e.g. EasyPost/Shippo carrier slug")
    active = models.BooleanField(default=True)

    class Meta:
        unique_together = [("code", "api_slug")]
        ordering = ["name"]

    def __str__(self):
        return self.name


class ShippingMethod(models.Model):
    """
    Your storefront's shipping options (Economy, Express, etc.).
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    carrier = models.ForeignKey(ShippingCarrier, on_delete=models.PROTECT, related_name="methods")
    name = models.CharField(max_length=120)  # e.g. "Ground", "2-Day", "Next Day"
    service_code = models.CharField(max_length=80, blank=True, help_text="Carrier service code if applicable")
    est_min_days = models.PositiveSmallIntegerField(null=True, blank=True)
    est_max_days = models.PositiveSmallIntegerField(null=True, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        unique_together = [("carrier", "name"), ("carrier", "service_code")]
        ordering = ["carrier__name", "name"]

    def __str__(self):
        return f"{self.carrier.name} — {self.name}"


class Shipment(TimeStampedModel):
    """
    One physical parcel belonging to an order. Orders can have multiple shipments (partial fulfillment).
    """
    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")           # created but not packed/labelled
        READY = "ready", _("Ready to Ship")         # label purchased, awaiting handoff
        IN_TRANSIT = "in_transit", _("In Transit")
        OUT_FOR_DELIVERY = "out_for_delivery", _("Out for Delivery")
        DELIVERED = "delivered", _("Delivered")
        RETURNED = "returned", _("Returned to Sender")
        CANCELLED = "cancelled", _("Cancelled")
        EXCEPTION = "exception", _("Exception")     # lost/damaged/etc.

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey("Order", on_delete=models.CASCADE, related_name="shipments")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)

    # Destination snapshot (avoid mutation when user edits address later)
    to_name = models.CharField(max_length=120)
    to_line1 = models.CharField(max_length=200)
    to_line2 = models.CharField(max_length=200, blank=True)
    to_city = models.CharField(max_length=120)
    to_state = models.CharField(max_length=120, blank=True)
    to_postal_code = models.CharField(max_length=30, blank=True)
    to_country = models.CharField(max_length=2)
    to_phone = models.CharField(max_length=40, blank=True)
    email = models.EmailField(blank=True)

    # Carrier & label
    carrier = models.ForeignKey(ShippingCarrier, on_delete=models.PROTECT, related_name="shipments")
    method = models.ForeignKey(ShippingMethod, null=True, blank=True, on_delete=models.SET_NULL, related_name="shipments")
    tracking_number = models.CharField(max_length=80, blank=True, db_index=True)
    tracking_url = models.URLField(max_length=500, blank=True)
    label_url = models.URLField(max_length=500, blank=True)
    label_cost = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    currency = models.CharField(max_length=10, default="NGN")
    # package details (optional but handy for rates)
    weight_grams = models.PositiveIntegerField(null=True, blank=True)
    length_cm = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    width_cm = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    height_cm = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    notes = models.TextField(blank=True)

    # Timestamps from carrier events
    shipped_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["tracking_number"]),
        ]

    def __str__(self):
        return f"Shipment {self.pk} for Order {self.order_id}"

    @property
    def is_active(self) -> bool:
        return self.status not in {self.Status.DELIVERED, self.Status.RETURNED, self.Status.CANCELLED}


class ShipmentItem(TimeStampedModel):
    """
    Which order items went into this parcel; supports partial shipments.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    shipment = models.ForeignKey(Shipment, on_delete=models.CASCADE, related_name="items")
    order_item = models.ForeignKey("OrderItem", on_delete=models.PROTECT, related_name="shipment_items")
    quantity = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])

    class Meta:
        unique_together = [("shipment", "order_item")]
        ordering = ["created_at"]


class TrackingEvent(TimeStampedModel):
    """
    Normalized tracking timeline (fed by carrier webhooks or polling).
    """
    class EventCode(models.TextChoices):
        INFO_RECEIVED = "info_received", _("Label Created")
        ACCEPTED = "accepted", _("Accepted by Carrier")
        IN_TRANSIT = "in_transit", _("In Transit")
        OUT_FOR_DELIVERY = "out_for_delivery", _("Out for Delivery")
        DELIVERED = "delivered", _("Delivered")
        FAILURE = "failure", _("Delivery Failure")
        EXCEPTION = "exception", _("Exception")
        RETURNED = "returned", _("Returned to Sender")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    shipment = models.ForeignKey(Shipment, on_delete=models.CASCADE, related_name="events")
    event_code = models.CharField(max_length=30, choices=EventCode.choices, db_index=True)
    description = models.CharField(max_length=300, blank=True)
    # Location at event time
    city = models.CharField(max_length=120, blank=True)
    state = models.CharField(max_length=120, blank=True)
    country = models.CharField(max_length=2, blank=True)
    postal_code = models.CharField(max_length=30, blank=True)
    occurred_at = models.DateTimeField(db_index=True)
    # Provider refs
    carrier_status = models.CharField(max_length=120, blank=True)
    raw_payload = models.JSONField(blank=True, null=True)  # store webhook bodies if you like

    class Meta:
        ordering = ["occurred_at", "created_at"]


# --- RETURNS (RMA) ---

class ReturnAuthorization(TimeStampedModel):
    """
    A customer's request to return one or more shipped items.
    """
    class Status(models.TextChoices):
        REQUESTED = "requested", _("Requested")
        APPROVED = "approved", _("Approved")
        REJECTED = "rejected", _("Rejected")
        IN_TRANSIT = "in_transit", _("In Transit to Warehouse")
        RECEIVED = "received", _("Received")
        REFUNDED = "refunded", _("Refunded")
        PARTIALLY_REFUNDED = "partially_refunded", _("Partially Refunded")
        CANCELLED = "cancelled", _("Cancelled")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey("Order", on_delete=models.CASCADE, related_name="return_authorizations")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.REQUESTED, db_index=True)
    reason = models.CharField(max_length=200, blank=True)
    customer_message = models.TextField(blank=True)
    merchant_notes = models.TextField(blank=True)

    # Return shipping (optional pre-paid label you issue)
    rma_number = models.CharField(max_length=40, unique=True, db_index=True)
    carrier = models.ForeignKey(ShippingCarrier, null=True, blank=True, on_delete=models.SET_NULL)
    tracking_number = models.CharField(max_length=80, blank=True)
    tracking_url = models.URLField(max_length=500, blank=True)
    label_url = models.URLField(max_length=500, blank=True)

    refund_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    currency = models.CharField(max_length=10, default="NGN")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"RMA {self.rma_number} for Order {self.order_id}"


class ReturnItem(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    rma = models.ForeignKey(ReturnAuthorization, on_delete=models.CASCADE, related_name="items")
    order_item = models.ForeignKey("OrderItem", on_delete=models.PROTECT, related_name="return_items")
    quantity = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    condition = models.CharField(max_length=80, blank=True)  # e.g. "Sealed", "Opened", "Damaged"
    reason = models.CharField(max_length=200, blank=True)
    approved = models.BooleanField(default=False)
    refund_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    class Meta:
        unique_together = [("rma", "order_item")]
        ordering = ["created_at"]
