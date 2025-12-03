from django.db import models
from django.conf import settings
from core.models import TimeStampedModel, NamedModel
from billing.models import OrganizationSubscription, SubscriptionPlan, SubscriptionInvoice
from datetime import timedelta
import time
import secrets
from decimal import Decimal

from django.db import models, transaction
from django.utils import timezone
from django.core.exceptions import ValidationError

# import your models (adjust import paths)
from orgs.models import OrganizationMembership, Organization

class Classroom(NamedModel):
    organization = models.ForeignKey("orgs.Organization", on_delete=models.CASCADE, related_name="classrooms")
    code = models.CharField(max_length=32, blank=True)
    teachers = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name="teaching_classrooms", blank=True)

    class Meta:
        unique_together = ("organization", "name")

class Subject(NamedModel):
    organization = models.ForeignKey("orgs.Organization", on_delete=models.CASCADE, related_name="subjects")
    code = models.CharField(max_length=32, blank=True)

    class Meta:
        unique_together = ("organization", "name")

class StudentProfile(TimeStampedModel):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="student_profile")
    organization = models.ForeignKey("orgs.Organization", on_delete=models.CASCADE, related_name="students", blank=True, null=True)
    current_classroom = models.ForeignKey(Classroom, on_delete=models.SET_NULL, null=True, blank=True)
    admission_no = models.CharField(max_length=64, blank=True)
    dob = models.DateField(null=True, blank=True)

    def __str__(self):
        return f"Student: {self.user.get_full_name() or self.user.username}"

class Language(models.Model):
    language_name = models.CharField(max_length=225)
    active = models.BooleanField(default=False)

class TeacherProfile(TimeStampedModel):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="teacher_profile")
    organization = models.ForeignKey("orgs.Organization", on_delete=models.CASCADE, related_name="teachers")
    bio = models.TextField(blank=True)
    experience = models.PositiveIntegerField(default=0)
    languages = models.ManyToManyField(Language, blank=True)
    specialties = models.ManyToManyField(Subject, blank=True)

    def __str__(self):
        return f"Teacher: {self.user.get_full_name() or self.user.username} Email: {self.user.email}"



class ParentProfile(TimeStampedModel):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="parent_profile")
    organization = models.ForeignKey("orgs.Organization", on_delete=models.CASCADE, related_name="parents",
    blank=True, null=True)
    organization_subscription = models.ForeignKey(
        OrganizationSubscription,
        on_delete=models.CASCADE,
        related_name="parent_subs",
        blank=True,
        null=True,
    )
    address = models.TextField(blank=True)

    # use TimeStampedModel.created_at (no new field needed)
    last_billed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Parent: {self.user.get_full_name() or self.user.username}"

    def _billing_period_days(self):
        """Return billing period in days (int). default to 30 if parsing fails."""
        plan = getattr(self.organization_subscription, "plan", None)
        if not plan:
            return 30
        try:
            return int(plan.billing_period)
        except Exception:
            return 30

    def _invoice_number_for(self, issued_at):
        """Generate a unique invoice number — change format if desired."""
        ts = int(time.time())
        token = secrets.token_hex(4)
        sub_id = self.organization_subscription.id if self.organization_subscription else "none"
        return f"PINV-{sub_id}-{self.id}-{ts}-{token}"

    def generate_subscription_invoices(self, now=None):
        now = now or timezone.now()

        subscription = self.organization_subscription
        if not subscription:
            return []

        if subscription.status != OrganizationSubscription.Status.ACTIVE:
            return []

        plan = subscription.plan
        billing_days = self._billing_period_days()

        if self.last_billed_at:
            next_due = self.last_billed_at + timedelta(days=billing_days)
        else:
            next_due = getattr(self, "created_at", None) or now

        if timezone.is_naive(next_due):
            next_due = timezone.make_aware(next_due)

        created_invoices = []
        max_iterations = 24
        iterations = 0
        
        """
        sub_end_datetime = None
        if subscription.end_date:
            sub_end_datetime = timezone.make_aware(
                datetime.datetime.combine(subscription.end_date, datetime.time.max)
            )

        """
        # Prepare/get parent membership once (we'll attach it to all created invoices)
        # Option: create membership automatically if not present.
        membership, _ = OrganizationMembership.objects.get_or_create(
            user=self.user,
            organization=self.organization,
            role=OrganizationMembership.Role.PARENT,
            defaults={"is_active": True}
        )
        # Note: get_or_create honors the unique_together ("user","organization","role")
        print(iterations, " iterations ",max_iterations)
        while iterations < max_iterations and next_due <= now:
            #if sub_end_datetime and next_due > sub_end_datetime:
                #break

            issued_at = next_due
            amount = Decimal(getattr(plan, "price", 0) or 0)

            inv = SubscriptionInvoice(
                organization_membership=membership,   # attach parent membership
                subscription=subscription,
                number=self._invoice_number_for(issued_at),
                amount=amount,
                currency=getattr(subscription, "currency", "NGN") if hasattr(subscription, "currency") else "NGN",
                issued_at=issued_at,
                due_at=issued_at + timedelta(days=billing_days),
                status=SubscriptionInvoice.Status.ACTIVE,
                meta={"generated_for": "parent_profile", "parent_profile_id": self.id},
            )

            with transaction.atomic():
                inv.save()                    # validation will now accept parent role
                created_invoices.append(inv)
                self.last_billed_at = issued_at
                self.save(update_fields=["last_billed_at"])

            next_due = next_due + timedelta(days=billing_days)
            iterations += 1

        return created_invoices

class ParentChildLink(TimeStampedModel):
    parent = models.ForeignKey(ParentProfile, on_delete=models.CASCADE, related_name="children_links")
    student = models.ForeignKey(StudentProfile, on_delete=models.CASCADE, related_name="parent_links")
    relationship = models.CharField(max_length=64, blank=True)

    class Meta:
        unique_together = ("parent", "student")
