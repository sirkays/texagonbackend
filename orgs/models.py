from django.db import models
from django.conf import settings
from core.models import TimeStampedModel, NamedModel

class Organization(NamedModel):
    class VideoConferencing(models.TextChoices):
        KONNECT = "konnect", "Konnect"
        LIVE = "live", "Live (Legacy)"

    slug = models.SlugField(unique=True)
    logo = models.ImageField(upload_to="org_logos/", blank=True, null=True)
    address = models.TextField(blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=100, blank=True)
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=32, blank=True)
    is_active = models.BooleanField(default=True)
    year = models.CharField(default="2026")
    allow_unsubscribed_users = models.BooleanField(default=False)
    allow_public_cert_request = models.BooleanField(
        default=False,
        help_text="When enabled, this organisation appears in the public certificate request form.",
    )
    video_conferencing = models.CharField(
        max_length=16,
        choices=VideoConferencing.choices,
        default=VideoConferencing.KONNECT,
        help_text="Video conferencing provider used by this organisation.",
    )

    class Meta:
        indexes = [models.Index(fields=["slug"])]

    @property
    def active_subscription(self):
        return self.subscriptions.filter(status="active").order_by("-end_date").first()

class OrganizationMembership(TimeStampedModel):
    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        ADMIN = "admin", "Admin"
        TEACHER = "teacher", "Teacher"
        STUDENT = "student", "Student"
        PARENT = "parent", "Parent"
        STAFF = "staff", "Staff"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships")
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=16, choices=Role.choices)
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = ("user", "organization", "role")
        indexes = [models.Index(fields=["organization", "role"])]

    def __str__(self):
        return f"{self.user} @ {self.organization} ({self.role})"

    @classmethod
    def fetch_defaults(cls, user):
        org = Organization.objects.get_or_create(
            slug="default",
        )
        return cls.objects.get_or_create(
            user=user,
            organization=org,
            role="owner",
        )

class AcademicSession(NamedModel):
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="sessions")
    start_date = models.DateField()
    end_date = models.DateField()
    is_current = models.BooleanField(default=False)

    def save(self, *args, **kwargs):
        if self.is_current:
            # Unset is_current for all other sessions in the same organization
            AcademicSession.objects.filter(
                organization=self.organization, 
                is_current=True
            ).exclude(pk=self.pk).update(is_current=False)
        super().save(*args, **kwargs)

    class Meta:
        unique_together = ("organization", "name")
        ordering = ["-start_date"]

