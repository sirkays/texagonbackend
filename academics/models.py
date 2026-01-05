from __future__ import annotations
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





def certificate_pdf_upload_to(instance: "EnrollmentCertificate", filename: str) -> str:
    y = timezone.localdate().year
    org_id = instance.organization_id or "unknown"
    return f"certificates/org_{org_id}/{y}/{instance.number or 'draft'}.pdf"


class EnrollmentCertificate(models.Model):
    """
    Certificate issued for a student's Enrollment in a Course.
    Download becomes available ONLY after 7 days from acquired_at (issued_at).
    """

    class Status(models.TextChoices):
        ISSUED = "issued", "Issued"
        REVOKED = "revoked", "Revoked"

    # Core links
    organization = models.ForeignKey(
        "orgs.Organization",
        on_delete=models.CASCADE,
        related_name="enrollment_certificates",
        db_index=True,
    )
    enrollment = models.OneToOneField(
        "learning.Enrollment",
        on_delete=models.CASCADE,
        related_name="certificate",
        help_text="One certificate per enrollment.",
    )
    student = models.ForeignKey(
        "academics.StudentProfile",
        on_delete=models.CASCADE,
        related_name="enrollment_certificates",
        db_index=True,
    )
    course = models.ForeignKey(
        "learning.Course",
        on_delete=models.CASCADE,
        related_name="enrollment_certificates",
        db_index=True,
    )

    # Display fields
    title = models.CharField(max_length=255, default="Certificate of Completion")
    description = models.TextField(blank=True)

    # Public identifiers
    number = models.CharField(max_length=32, unique=True, db_index=True, editable=False)
    verification_token = models.CharField(max_length=64, unique=True, db_index=True, editable=False)

    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ISSUED, db_index=True)

    # When the student "acquired" (earned) the certificate
    acquired_at = models.DateTimeField(default=timezone.now, db_index=True)

    # Download rule (7 days)
    download_after_days = models.PositiveSmallIntegerField(default=7)

    # Optional generated file
    pdf_file = models.FileField(upload_to=certificate_pdf_upload_to, blank=True, null=True)

    # Issuer info (optional)
    issued_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="issued_enrollment_certificates",
    )

    meta = models.JSONField(default=dict, blank=True)

    # Revocation fields
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_reason = models.CharField(max_length=255, blank=True)

    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-acquired_at"]
        indexes = [
            models.Index(fields=["organization", "course", "acquired_at"]),
            models.Index(fields=["student", "acquired_at"]),
            models.Index(fields=["status", "acquired_at"]),
        ]
        constraints = [
            # Ensure revoked_at is set iff status == revoked (soft safety)
            models.CheckConstraint(
                name="enroll_cert_revoked_requires_timestamp",
                check=models.Q(status="issued", revoked_at__isnull=True)
                | models.Q(status="revoked", revoked_at__isnull=False),
            ),
            # Unique certificate per (student, course) per enrollment is already enforced by OneToOne enrollment.
        ]

    def __str__(self) -> str:
        return f"{self.number} — enrollment={self.enrollment_id} ({self.status})"

    @staticmethod
    def _generate_number() -> str:
        return f"CERT-{secrets.token_hex(4).upper()}-{secrets.token_hex(2).upper()}"

    @staticmethod
    def _generate_verification_token() -> str:
        return secrets.token_urlsafe(32)[:64]

    def clean(self):
        super().clean()

        # Keep data consistent with enrollment
        if self.enrollment_id:
            # enrollment.student is a StudentProfile per your model
            if self.student_id and self.enrollment.student_id != self.student_id:
                raise ValidationError({"student": "student must match enrollment.student."})

            if self.course_id and self.enrollment.course_id != self.course_id:
                raise ValidationError({"course": "course must match enrollment.course."})

        # Ensure org matches student/course if those have organization
        if self.organization_id:
            if getattr(self.student, "organization_id", None) and self.student.organization_id != self.organization_id:
                raise ValidationError({"organization": "organization must match student.organization."})
            if getattr(self.course, "organization_id", None) and self.course.organization_id != self.organization_id:
                raise ValidationError({"organization": "organization must match course.organization."})

        # Download delay must be >= 0
        if self.download_after_days is not None and self.download_after_days < 0:
            raise ValidationError({"download_after_days": "Must be >= 0."})

        if self.status == self.Status.REVOKED and not self.revoked_at:
            raise ValidationError({"revoked_at": "revoked_at is required when status is REVOKED."})

    def save(self, *args, **kwargs):
        # Auto-fill from enrollment if not provided (nice DX)
        if self.enrollment_id:
            if not self.student_id:
                self.student_id = self.enrollment.student_id
            if not self.course_id:
                self.course_id = self.enrollment.course_id

            # If your Course has organization, prefer it. Else fall back to student org.
            if not self.organization_id:
                org_id = getattr(self.enrollment.course, "organization_id", None) or getattr(
                    getattr(self.enrollment.student, "organization", None), "id", None
                )
                if org_id:
                    self.organization_id = org_id

        if not self.number:
            for _ in range(5):
                cand = self._generate_number()
                if not EnrollmentCertificate.objects.filter(number=cand).exists():
                    self.number = cand
                    break

        if not self.verification_token:
            for _ in range(5):
                cand = self._generate_verification_token()
                if not EnrollmentCertificate.objects.filter(verification_token=cand).exists():
                    self.verification_token = cand
                    break

        if self.status == self.Status.REVOKED and self.revoked_at is None:
            self.revoked_at = timezone.now()

        self.full_clean()
        return super().save(*args, **kwargs)

    # --- Download gating ---

    @property
    def downloadable_at(self):
        return self.acquired_at + timedelta(days=int(self.download_after_days or 0))

    @property
    def can_download(self) -> bool:
        if self.status != self.Status.ISSUED:
            return False
        return timezone.now() >= self.downloadable_at

    def assert_can_download(self):
        """
        Call this in your download view before serving the pdf.
        """
        if self.status != self.Status.ISSUED:
            raise ValidationError("Certificate is not available (revoked).")
        if not self.can_download:
            raise ValidationError(
                f"Certificate will be downloadable on {timezone.localtime(self.downloadable_at):%Y-%m-%d %H:%M}."
            )

    def revoke(self, reason: str = "", by_user=None):
        self.status = self.Status.REVOKED
        self.revoked_at = timezone.now()
        self.revoked_reason = (reason or "")[:255]
        if by_user is not None:
            self.issued_by_user = by_user
        self.save(update_fields=["status", "revoked_at", "revoked_reason", "issued_by_user", "updated_at"])





def org_cert_signature_upload_to(instance, filename):
    # e.g. org_cert_signatures/org_12/director_1.png
    org_id = getattr(instance.organization, "id", "unknown")
    return f"org_cert_signatures/org_{org_id}/{filename}"


class OrganizationCertificateSignatures(models.Model):
    """
    Stores certificate director signatures for an organization.
    Exactly one row per organization.
    """
    organization = models.OneToOneField(
        "orgs.Organization",
        on_delete=models.CASCADE,
        related_name="certificate_signatures",
        db_index=True,
    )

    # Director 1
    director_1_name = models.CharField(max_length=255, blank=True)
    director_1_title = models.CharField(max_length=255, blank=True, default="Director")
    director_1_signature = models.ImageField(
        upload_to=org_cert_signature_upload_to, blank=True, null=True
    )

    # Director 2
    director_2_name = models.CharField(max_length=255, blank=True)
    director_2_title = models.CharField(max_length=255, blank=True, default="Director")
    director_2_signature = models.ImageField(
        upload_to=org_cert_signature_upload_to, blank=True, null=True
    )

    meta = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Certificate Signatures — Org {self.organization_id}"
