from __future__ import annotations

import uuid
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from nanoid import generate

from core.models import TimeStampedModel, NamedModel
from orgs.models import OrganizationMembership, Organization
from store.models import Product, Payment
from live.models import TutoringBooking


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
    start_date = models.DateField(blank=True, null=True)
    end_date = models.DateField(blank=True, null=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    auto_renew = models.BooleanField(default=True)
    payment_method = models.CharField(max_length=64, blank=True)
    meta = models.JSONField(default=dict, blank=True)

    @classmethod
    def get_lastest_org_sub(cls, organization):
        sub = cls.objects.filter(organization=organization)
        if sub.exists():
            return sub.last()
        plan = SubscriptionPlan.objects.all()
        if not plan:
            plan = SubscriptionPlan.objects.create(
                price=10000,
            )
        else:
            plan = plan.last()
        return cls.objects.create(
            organization=organization,
            plan=plan
        )

class SubscriptionInvoice(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "pending"
        OPEN = "open", "open"
        PAID = "paid", "paid"
        VOID = "void", "void"
        UNCOLLECTIBLE = "uncollectible", "uncollectible"
        ACTIVE = "active", "active"


    def invoice_number():
        return f"INV-{generate('0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ', 10)}"

    organization_membership = models.ForeignKey(OrganizationMembership, on_delete=models.CASCADE, 
    related_name="my_invoices", blank=True, null=True)
    subscription = models.ForeignKey(OrganizationSubscription, on_delete=models.CASCADE, related_name="invoices",
    blank=True, null=True)
    number = models.CharField(
        max_length=20,
        unique=True,
        default=invoice_number,
        editable=False
    )
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
    class Provider(models.TextChoices):
        FLUTTERWAVE = "flutterwave", _("Flutterwave")
        PAYSTACK = "paystack", _("Paystack")

    class Status(models.TextChoices):
        SUCCESS = "success", "success"
        FAILED = "failed", "failed"
        CANCELLED = "cancelled", "Cancelled"
        INPROGRESS = "inprogress", "inprogress"
        CREATED = "created", "created"

    invoice = models.ForeignKey(
        SubscriptionInvoice, on_delete=models.CASCADE, related_name="payments",
    )
    provider = models.CharField(max_length=20, choices=Provider.choices, default=Provider.FLUTTERWAVE)
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


class InvoiceType(models.Model):
    class Paytype(models.TextChoices):
        TUTOR = "tutor","tutor",
        SUBSCRIPTION = "subscription","subscription"
        STORE = "store", "store"
    invoice = models.OneToOneField(SubscriptionInvoice, on_delete=models.CASCADE)
    invoice_type = models.CharField(max_length=16, choices=Paytype.choices, default=Paytype.SUBSCRIPTION)
    object_id = models.CharField(max_length=250,blank=True, null=True)
    object_type = models.CharField(max_length=10, blank=True, null=True)
    meta = models.JSONField(default=dict, blank=True)





class UserAccountSubscription(TimeStampedModel):
    """
    Tracks subscription at the USER level (student/parent/etc), scoped to an organization.
    This is your source of truth for: is this user subscribed? is it expired?
    """

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        EXPIRED = "expired", "Expired"
        CANCELLED = "cancelled", "Cancelled"
        PAST_DUE = "past_due", "Past Due"

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="user_subscriptions"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="subscriptions"
    )

    # plan controls pricing + billing period (days)
    plan = models.ForeignKey(
        SubscriptionPlan, on_delete=models.PROTECT, related_name="user_subscriptions"
    )

    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.ACTIVE, db_index=True
    )

    start_at = models.DateTimeField(default=timezone.now, db_index=True)
    end_at = models.DateTimeField(null=True, blank=True, db_index=True)

    auto_renew = models.BooleanField(default=True)

    # Optional: who pays (useful for your parent->child billing)
    billed_to_parent = models.ForeignKey(
        "academics.ParentProfile",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="billed_child_subscriptions",
    )

    # Optional: amounts “locked” per subscription (if you want plan changes not to affect existing subs)
    amount = models.DecimalField(
        max_digits=12, decimal_places=2,
        validators=[MinValueValidator(0)],
        default=Decimal("0.00")
    )
    currency = models.CharField(max_length=8, default="NGN")

    meta = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["organization", "user", "status"]),
            models.Index(fields=["organization", "status", "end_at"]),
        ]
        constraints = [
            # One active subscription per user per org (prevents duplicates)
            models.UniqueConstraint(
                fields=["organization", "user"],
                condition=Q(status="active"),
                name="uniq_active_user_subscription_per_org",
            )
        ]

    def __str__(self):
        return f"{self.user_id} @ org {self.organization_id} ({self.status})"

    @property
    def is_expired(self) -> bool:
        if self.status in {self.Status.EXPIRED, self.Status.CANCELLED}:
            return True
        if self.end_at and timezone.now() >= self.end_at:
            return True
        return False

    def refresh_status(self, save=True) -> str:
        """
        Update status based on end_at.
        """
        if self.status == self.Status.CANCELLED:
            return self.status

        if self.end_at and timezone.now() >= self.end_at:
            self.status = self.Status.EXPIRED
        else:
            self.status = self.Status.ACTIVE

        if save:
            self.save(update_fields=["status", "updated_at"])
        return self.status


class Complaint(models.Model):  # or inherit your TimeStampedModel
    class Status(models.TextChoices):
        OPEN = "open", _("Open")
        IN_PROGRESS = "in_progress", _("In Progress")
        RESOLVED = "resolved", _("Resolved")
        CLOSED = "closed", _("Closed")

    class Priority(models.TextChoices):
        LOW = "low", _("Low")
        MEDIUM = "medium", _("Medium")
        HIGH = "high", _("High")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Human-friendly code like COMP-000123 (unique, indexed)
    code = models.CharField(max_length=32, unique=True, db_index=True, editable=False)

    title = models.CharField(max_length=200)
    description = models.TextField()

    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.OPEN, db_index=True
    )
    priority = models.CharField(
        max_length=10, choices=Priority.choices, default=Priority.MEDIUM, db_index=True
    )

    # Link to either a one-off Payment (e.g., product order) OR a SubscriptionPayment (invoice payment)
    payment = models.ForeignKey(
        Payment, null=True, blank=True, on_delete=models.SET_NULL, related_name="complaints"
    )
    subscription_payment = models.ForeignKey(
        SubscriptionPayment, null=True, blank=True, on_delete=models.SET_NULL, related_name="complaints"
    )

    # Who raised the complaint and who is assigned to handle it
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="created_complaints"
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="assigned_complaints"
    )

    # Timestamps (aligning with your React component fields)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            # Ensure at most ONE of the two payment links is set
            models.CheckConstraint(
                name="complaint_single_transaction_link",
                check=(
                    # (payment is null AND subscription is null) OR (exactly one is not null)
                    (models.Q(payment__isnull=True) & models.Q(subscription_payment__isnull=True)) |
                    (models.Q(payment__isnull=False) & models.Q(subscription_payment__isnull=True)) |
                    (models.Q(payment__isnull=True) & models.Q(subscription_payment__isnull=False))
                ),
            )
        ]

    def __str__(self):
        return f"{self.code} — {self.title}"

    @property
    def transaction_identifier(self) -> str | None:
        """
        A small helper to mirror the UI's 'transactionId' chip.
        Returns something like payment.provider_ref or subscription reference.
        """
        if self.payment:
            return self.payment.provider_ref or str(self.payment.id)
        if self.subscription_payment:
            return self.subscription_payment.reference
        return None

    @property
    def responses_count(self) -> int:
        return self.responses.count()

    def save(self, *args, **kwargs):
        # Generate the human-readable code once, atomically, with zero race conditions.
        if not self.code:
            with transaction.atomic():
                # pessimistic lock via a counter row would be ideal;
                # for simplicity, derive from total count + 1 within a transaction
                last_id = (
                    Complaint.objects.select_for_update()
                    .order_by("-created_at")
                    .values_list("id", flat=True)
                    .first()
                )
                seq = Complaint.objects.count() + 1
                self.code = f"COMP-{seq:06d}"
        super().save(*args, **kwargs)


class ComplaintResponse(models.Model):
    class Role(models.TextChoices):
        USER = "user", _("User")
        SUPPORT = "support", _("Support")
        ADMIN = "admin", _("Admin")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    complaint = models.ForeignKey(
        Complaint, on_delete=models.CASCADE, related_name="responses"
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="complaint_responses"
    )
    # Redundant author_name to preserve display if the user is later deleted/NULL
    author_name = models.CharField(max_length=150, blank=True)

    role = models.CharField(max_length=16, choices=Role.choices, default=Role.SUPPORT)
    message = models.TextField()

    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ("created_at",)

    def save(self, *args, **kwargs):
        if not self.author_name and self.author:
            self.author_name = getattr(self.author, "get_full_name", None) and self.author.get_full_name() or self.author.username
        super().save(*args, **kwargs)


def attachment_upload_to(instance: "ComplaintAttachment", filename: str) -> str:
    # e.g., complaints/COMP-000123/2024/10/filename.pdf
    code = instance.complaint.code if instance.complaint_id else "uncategorized"
    now = timezone.now()
    return f"complaints/{code}/{now.year}/{now.month:02d}/{filename}"


class ComplaintAttachment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    complaint = models.ForeignKey(
        Complaint, on_delete=models.CASCADE, related_name="attachments"
    )
    file = models.FileField(upload_to=attachment_upload_to, max_length=1024)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="complaint_attachments"
    )
    uploaded_at = models.DateTimeField(default=timezone.now)

    original_name = models.CharField(max_length=255, blank=True)
    content_type = models.CharField(max_length=100, blank=True)



