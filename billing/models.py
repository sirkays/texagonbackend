from django.db import models
from core.models import TimeStampedModel, NamedModel
from django.utils import timezone
from django.core.validators import MinValueValidator
from orgs.models import OrganizationMembership

class SubscriptionPlan(NamedModel):
    price = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])
    billing_period = models.CharField(max_length=8, default="30") # BILLING PERIOD IS CALCULATED IN DAYS. DEFAULT IS 30 DAYS
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
    class Status(models.TextChoices):
        OPEN = "open", "open"
        PAID = "paid", "paid"
        VOID = "void", "void"
        UNCOLLECTIBLE = "uncollectible", "uncollectible"
        ACTIVE = "active", "active"
    organization_membership = models.ForeignKey(OrganizationMembership, on_delete=models.CASCADE, related_name="my_invoices", blank=True, null=True)
    subscription = models.ForeignKey(OrganizationSubscription, on_delete=models.CASCADE, related_name="invoices")
    number = models.CharField(max_length=64, unique=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=8, default="NGN")
    issued_at = models.DateTimeField(default=timezone.now)
    due_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)  # open, paid, void, 
    meta = models.JSONField(default=dict, blank=True)
    transaction_id = models.CharField(max_length=450, blank=True, null=True)

    def clean(self):
        super().clean()
        membership = self.organization_membership
        # Allow invoices attached to either teachers or parents
        allowed_roles = {
            OrganizationMembership.Role.PARENT,
        }
        if membership and membership.role not in allowed_roles:
            raise ValidationError({
                'organization_membership': 'organization_membership must be a Teacher or a Parent.'
            })

    def save(self, *args, **kwargs):
        # ensure full_clean runs before saving (so DB never gets invalid data)
        self.full_clean()
        super().save(*args, **kwargs)



class SubscriptionPayment(TimeStampedModel):
    class Status(models.TextChoices):
        SUCCESS = "success", "success"
        FAILED = "failed", "failed"
        CANCELLED = "cancelled", "Cancelled"
        INPROGRESS = "inprogress", "inprogress"
        CREATED = "created", "created"

    invoice = models.ForeignKey(SubscriptionInvoice, on_delete=models.CASCADE, related_name="payments")
    reference = models.CharField(max_length=128, unique=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=8, default="NGN")
    method = models.CharField(max_length=32, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.CREATED)  # success, failed, pending
    paid_at = models.DateTimeField(default=timezone.now)
    meta = models.JSONField(default=dict, blank=True)
    transaction_id = models.CharField(max_length=450, blank=True, null=True)
    redirect_url =  models.URLField(max_length=1250, blank=True, null=True)

    def change_current_trans(self, status):
        self.status = status
        self.paid_at = timezone.now()
        self.save()