from django.db import models
from core.models import TimeStampedModel

class LiveSession(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        CONFIRMED = "started", "Started"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"
    course = models.ForeignKey("learning.Course", on_delete=models.CASCADE, related_name="live_sessions")
    title = models.CharField(max_length=255)
    scheduled_at = models.DateTimeField()
    duration_minutes = models.PositiveIntegerField(default=60)
    host = models.ForeignKey("academics.TeacherProfile", on_delete=models.PROTECT, related_name="hosted_live_sessions")
    join_url = models.URLField(blank=True)
    recording_url = models.URLField(blank=True)
    meta = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    active = models.BooleanField(default=True)

class TutoringBooking(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        CONFIRMED = "confirmed", "Confirmed"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    organization = models.ForeignKey("orgs.Organization", on_delete=models.CASCADE, related_name="tutoring_bookings")
    teacher = models.ForeignKey("academics.TeacherProfile", on_delete=models.PROTECT, related_name="tutoring_bookings")
    student = models.ForeignKey("academics.StudentProfile", on_delete=models.PROTECT, related_name="tutoring_bookings")
    scheduled_at = models.DateTimeField()
    duration_minutes = models.PositiveIntegerField(default=60)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    notes = models.TextField(blank=True)
