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
from accounts.models import User
from django.db.models import Max
from django.utils.dateparse import parse_datetime
from nanoid import generate # Import NanoID

class Classroom(NamedModel):
    USAGE_CHOICE = (
        ('public','public'),
        ('private','private'),
    )
    organization = models.ForeignKey("orgs.Organization", on_delete=models.CASCADE, related_name="classrooms")
    code = models.CharField(max_length=32, blank=True)
    teachers = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name="teaching_classrooms", blank=True)
    class_type = models.CharField(
        max_length=20,
        choices=USAGE_CHOICE,
        default='public',
    )

    class Meta:
        unique_together = ("organization", "name", "code")

class Subject(NamedModel):
    organization = models.ForeignKey("orgs.Organization", on_delete=models.CASCADE, related_name="subjects")
    code = models.CharField(max_length=32, blank=True)

    class Meta:
        unique_together = ("organization", "name")



class Language(models.Model):
    language_name = models.CharField(max_length=225)
    active = models.BooleanField(default=False)


class StudentProfile(TimeStampedModel):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="student_profile")
    organization = models.ForeignKey("orgs.Organization", on_delete=models.CASCADE, related_name="students", blank=True, null=True)
    current_classroom = models.ForeignKey(Classroom, on_delete=models.SET_NULL, null=True, blank=True)
    
    # Keeping unique=True is highly recommended if you enforce uniqueness
    admission_no = models.CharField(max_length=64, blank=True, null=True) 
    dob = models.DateField(null=True, blank=True)

    def save(self, *args, **kwargs):
        # Only generate if admission_no is empty AND an organization is attached
        if not self.admission_no and self.organization:
            
            org_year = self.organization.year
            # Custom alphabet: Numbers + Uppercase letters (removed lookalikes like 0, O, I, 1 for clarity if desired, but here is a standard alphanumeric mix)
            alphabet = '23456789ABCDEFGHJKLMNPQRSTUVWXYZ' 
            
            while True:
                # Generate a 6-character random string
                unique_str = generate(alphabet, 6)
                
                # Format: e.g., 2026/A4K9X2
                new_admission_no = f"{org_year}/{unique_str}"
                
                # Check for collisions; break the loop if unique
                if not StudentProfile.objects.filter(admission_no=new_admission_no).exists():
                    self.admission_no = new_admission_no
                    break

        # Call the parent class's save method
        super().save(*args, **kwargs)


    def __str__(self):
        return f"Student: {self.user.get_full_name() or self.user.username}"



    def check_session_data(self, request, _retry=False):
        from billing.models import UserAccountSubscription

        # ── Fast path: active subscription in DB ──────────────────────────
        if UserAccountSubscription.has_subscription(self.user, self.organization):
            return True

        # ── Session-cache path (only valid for cookie-session requests) ───
        # For token-authenticated API requests the Django session is always
        # empty, so skip the cache entirely and rely solely on the DB check.
        session_key = "allowed_courses_cache"
        session = getattr(request, "session", None)

        # If the session backend hasn't been initialised (token auth),
        # treat the student as not-allowed (subscription check already failed above).
        if session is None or not hasattr(session, "get"):
            return False

        # Load cache (populate if missing)
        cached_data = session.get(session_key)
        if cached_data is None:
            self.get_course_allowed(request)
            cached_data = session.get(session_key)

        if not cached_data:
            return False

        cached_date = cached_data.get("general_activation_date")
        date_cached_str = cached_data.get("date_cached")

        # If either is missing, treat as invalid
        if not (cached_date and date_cached_str):
            return False

        # Parse date_cached (prefer ISO-8601)
        try:
            from django.utils.dateparse import parse_datetime
            date_cached_dt = parse_datetime(date_cached_str)

            if date_cached_dt is None:
                raise ValueError("Could not parse date_cached")
            if timezone.is_naive(date_cached_dt):
                date_cached_dt = timezone.make_aware(date_cached_dt, timezone.get_current_timezone())

        except Exception:
            session.pop(session_key, None)
            return False

        # Expire after 1 hour
        if timezone.now() - date_cached_dt > timedelta(hours=1):
            session.pop(session_key, None)
            if _retry:
                return False
            return self.check_session_data(request, _retry=True)

        return True



    def get_course_allowed(self, request, **kwargs):
        from core.utils import resolve_season
        from billing.models import UserAccountSubscription

        is_session = kwargs.get("is_session", False)
        is_general_activation = kwargs.get("is_general_activation", False)

        now = timezone.now()
        session_key = "allowed_courses_cache"

        # 1️⃣ Check session cache
        cached_data = request.session.get(session_key)

        course_ids = []
        cached_date = None
        returned_count = 0

        # 2️⃣ User has active subscription or unrestricted access
        if (
            UserAccountSubscription.has_subscription(self.user, self.organization)
        ):
            returned_count = 1
            return True, returned_count

        # 3️⃣ Use cached data if still valid
        if cached_data:
            cached_date = cached_data.get("general_activation_date")
            course_ids = cached_data.get("course_ids", [])

            if isinstance(cached_date, str):
                cached_date = parse_datetime(cached_date)

            if cached_date and cached_date > now:
                if is_session:
                    returned_count = 2
                    return (course_ids, cached_date), returned_count
                return self.enrollments.filter(course_id__in=course_ids)

        # 4️⃣ Compute fresh queryset
        leaderboard_season = resolve_season(self.organization, now)

        queryset = self.enrollments.filter(
            leaderboard_season=leaderboard_season,
            course__course_type="public",
            completed_at__isnull=True,
            status="active",
            course__general_activation_date__isnull=False,
        )

        if is_general_activation:
            queryset = queryset.filter(course__general_activation=True)

        # 5️⃣ Extract course IDs and latest activation date
        course_ids = list(queryset.values_list("course_id", flat=True))

        max_activation_date = queryset.aggregate(
            Max("course__general_activation_date")
        )["course__general_activation_date__max"]

        # 6️⃣ Cache safely (serialize datetime)
        if max_activation_date:
            request.session[session_key] = {
                "course_ids": course_ids,
                "general_activation_date": max_activation_date.isoformat(),
                "date_cached":timezone.now().isoformat(),
            }

            #print(request.session.get("allowed_courses_cache"))

        # 7️⃣ Return based on mode
        if is_session:
            returned_count = 2
            return (course_ids, max_activation_date), returned_count

        return queryset



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
    user_subscription = models.ForeignKey("billing.UserAccountSubscription", on_delete=models.CASCADE, related_name="parent_subs",
    blank=True, null=True)
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


    leaderboard_season = models.ForeignKey(
        "gamification.LeaderboardSeason",
        on_delete=models.CASCADE,
        related_name="season_certificates",
        blank=True, null=True
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


    # academics/models.py (inside EnrollmentCertificate)

    def is_teacher_approved(self, user=None) -> bool:
        if user:
            return self.approvals.filter(user_type="teacher", approval=True, user=user).exists()
        return self.approvals.filter(user_type="teacher", approval=True).exists()

    def is_admin_approved(self, user=None) -> bool:
        if user:
            return self.approvals.filter(user_type="admin", approval=True, user=user).exists()
        return self.approvals.filter(user_type="admin", approval=True).exists()

    @property
    def fully_approved(self) -> bool:
        return self.is_teacher_approved() and self.is_admin_approved()

    @property
    def can_download(self) -> bool:
        if self.status != self.Status.ISSUED:
            return False
        if timezone.now() < self.downloadable_at:
            return False
        return self.fully_approved


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



class StudentEnrollmentCertificateApproval(TimeStampedModel):
    class UserType(models.TextChoices):
        TEACHER = "teacher", "teacher"
        ADMIN = "admin", "admin"

    certificate = models.ForeignKey(EnrollmentCertificate, on_delete=models.CASCADE, related_name="approvals")
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    approval = models.BooleanField(default=False)
    user_type = models.CharField(max_length=25, choices=UserType.choices, default=UserType.TEACHER)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["certificate", "user_type"],
                name="unique_approval_per_cert_per_role",
            )
        ]


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


# ─────────────────────────────────────────────────────────
# Teacher Report System
# ─────────────────────────────────────────────────────────

def report_video_upload_to(instance, filename):
    import os
    from datetime import datetime
    today = datetime.utcnow()
    safe_name = os.path.basename(filename)
    report_id = instance.report_id or 0
    return f"texagon/reports/{report_id}/videos/{today:%Y/%m/%d}/{safe_name}"


class TeacherReport(TimeStampedModel):
    """
    A report created by a teacher summarising student activities,
    CBT results, coding projects, and class activities.
    """
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"

    class RecipientMode(models.TextChoices):
        SELECTED = "selected", "Selected Students"
        COURSE = "course", "All in Course"
        CLASSROOM = "classroom", "All in Classroom"

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="teacher_reports",
    )
    teacher = models.ForeignKey(
        "academics.TeacherProfile",
        on_delete=models.CASCADE,
        related_name="created_reports",
    )
    course = models.ForeignKey(
        "learning.Course",
        on_delete=models.CASCADE,
        related_name="teacher_reports",
    )

    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, help_text="Teacher notes / description")
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.DRAFT
    )
    recipient_mode = models.CharField(
        max_length=16, choices=RecipientMode.choices, default=RecipientMode.SELECTED,
    )
    published_at = models.DateTimeField(null=True, blank=True)

    # Public share link token (for parent onboarding)
    share_token = models.CharField(max_length=64, unique=True, db_index=True, editable=False)

    # Optional: date range the report covers
    period_start = models.DateField(null=True, blank=True)
    period_end = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["organization", "teacher", "status"]),
            models.Index(fields=["course", "status"]),
        ]

    def __str__(self):
        return f"[Report] {self.title} by {self.teacher} ({self.status})"

    def save(self, *args, **kwargs):
        if not self.share_token:
            self.share_token = secrets.token_urlsafe(32)[:48]
        super().save(*args, **kwargs)


class ReportCBTItem(TimeStampedModel):
    """A CBT test included in a report, with a snapshot of the student's score."""
    report = models.ForeignKey(
        TeacherReport, on_delete=models.CASCADE, related_name="cbt_items",
    )
    test = models.ForeignKey(
        "assessments.Test", on_delete=models.CASCADE, related_name="+",
    )
    # Snapshot fields so report remains stable even if test data changes
    test_title = models.CharField(max_length=255, blank=True)
    total_marks = models.DecimalField(max_digits=7, decimal_places=2, default=0)

    class Meta:
        unique_together = ("report", "test")
        ordering = ["created_at"]

    def save(self, *args, **kwargs):
        if not self.test_title and self.test_id:
            self.test_title = self.test.title
        if not self.total_marks and self.test_id:
            self.total_marks = self.test.total_marks
        super().save(*args, **kwargs)


class ReportCodingItem(TimeStampedModel):
    """A coding project submission included in a report."""
    report = models.ForeignKey(
        TeacherReport, on_delete=models.CASCADE, related_name="coding_items",
    )
    lesson = models.ForeignKey(
        "learning.Lesson", on_delete=models.CASCADE, related_name="+",
    )
    # Snapshot
    lesson_title = models.CharField(max_length=255, blank=True)

    class Meta:
        unique_together = ("report", "lesson")
        ordering = ["created_at"]

    def save(self, *args, **kwargs):
        if not self.lesson_title and self.lesson_id:
            self.lesson_title = self.lesson.name
        super().save(*args, **kwargs)


class ReportActivity(TimeStampedModel):
    """Custom class activity added by the teacher."""
    report = models.ForeignKey(
        TeacherReport, on_delete=models.CASCADE, related_name="activities",
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    activity_date = models.DateField(null=True, blank=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "activity_date"]


class ReportVideo(TimeStampedModel):
    """Video attachment on a report (uploaded file or external URL)."""
    report = models.ForeignKey(
        TeacherReport, on_delete=models.CASCADE, related_name="videos",
    )
    title = models.CharField(max_length=255, blank=True)
    video_file = models.FileField(
        upload_to=report_video_upload_to, max_length=512,
        null=True, blank=True,
    )
    video_url = models.URLField(blank=True, help_text="External video URL (YouTube, etc.)")

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"[ReportVideo] {self.title or self.video_url}"


class ReportRecipient(TimeStampedModel):
    """
    Tracks which students received this report and per-student score snapshots.
    """
    report = models.ForeignKey(
        TeacherReport, on_delete=models.CASCADE, related_name="recipients",
    )
    student = models.ForeignKey(
        StudentProfile, on_delete=models.CASCADE, related_name="received_reports",
    )
    # Per-student CBT scores snapshot: { test_id: { score, total } }
    cbt_scores = models.JSONField(default=dict, blank=True)
    # Per-student coding scores snapshot: { lesson_id: { score, feedback, project_title } }
    coding_scores = models.JSONField(default=dict, blank=True)
    # Overall teacher remark for this specific student
    teacher_remark = models.TextField(blank=True)
    # Whether parent has viewed this report
    parent_viewed = models.BooleanField(default=False)
    parent_viewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ("report", "student")
        ordering = ["student__user__first_name"]
        indexes = [
            models.Index(fields=["student", "report"]),
        ]

    def __str__(self):
        return f"[Recipient] student={self.student_id} report={self.report_id}"
