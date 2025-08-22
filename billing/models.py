from django.db import models
from core.models import TimeStampedModel, NamedModel
from django.utils import timezone
from django.core.validators import MinValueValidator

class SubscriptionPlan(NamedModel):
    class Period(models.TextChoices):
        MONTH = "month", "Monthly"
        YEAR = "year", "Yearly"

    price = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])
    billing_period = models.CharField(max_length=8, choices=Period.choices, default=Period.MONTH)
    features = models.JSONField(default=list, blank=True)
    student_limit = models.PositiveIntegerField(default=0)  # 0 = unlimited

class OrganizationSubscription(TimeStampedModel):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        EXPIRED = "expired", "Expired"
        CANCELLED = "cancelled", "Cancelled"
        PAST_DUE = "past_due", "Past Due"

    organization = models.ForeignKey("orgs.Organization", on_delete=models.CASCADE, related_name="subscriptions")
    plan = models.ForeignKey(SubscriptionPlan, on_delete=models.PROTECT, related_name="subscriptions")
    start_date = models.DateField()
    end_date = models.DateField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    auto_renew = models.BooleanField(default=True)
    payment_method = models.CharField(max_length=64, blank=True)
    meta = models.JSONField(default=dict, blank=True)

class SubscriptionInvoice(TimeStampedModel):
    subscription = models.ForeignKey(OrganizationSubscription, on_delete=models.CASCADE, related_name="invoices")
    number = models.CharField(max_length=64, unique=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=8, default="NGN")
    issued_at = models.DateTimeField(default=timezone.now)
    due_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=16, default="open")  # open, paid, void, uncollectible
    meta = models.JSONField(default=dict, blank=True)

class SubscriptionPayment(TimeStampedModel):
    invoice = models.ForeignKey(SubscriptionInvoice, on_delete=models.CASCADE, related_name="payments")
    reference = models.CharField(max_length=128, unique=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=8, default="NGN")
    method = models.CharField(max_length=32, blank=True)
    status = models.CharField(max_length=16, default="success")  # success, failed, pending
    paid_at = models.DateTimeField(default=timezone.now)
    meta = models.JSONField(default=dict, blank=True)
