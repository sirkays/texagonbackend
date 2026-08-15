from django.db import models
from core.models import TimeStampedModel
from django.utils import timezone

class LiveSession(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        CONFIRMED = "started", "Started"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    class SessionType(models.TextChoices):
        SFU = "default", "Group Call (SFU)"
        LIVESTREAM = "livestream", "Live Class (Broadcast)"

    course = models.ForeignKey("learning.Course", on_delete=models.CASCADE, related_name="live_sessions", null=True, blank=True)
    title = models.CharField(max_length=255)
    scheduled_at = models.DateTimeField()
    duration_minutes = models.PositiveIntegerField(default=60)
    host = models.ForeignKey("academics.TeacherProfile", on_delete=models.PROTECT, related_name="hosted_live_sessions")
    join_url = models.URLField(blank=True)
    recording_url = models.URLField(blank=True)
    meta = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    active = models.BooleanField(default=True)
    is_public = models.BooleanField(default=False)
    is_room_open = models.BooleanField(default=True)
    session_type = models.CharField(
        max_length=16,
        choices=SessionType.choices,
        default=SessionType.SFU,
    )


class LiveSessionConfiguration(TimeStampedModel):
    """
    Global configuration for Live Sessions and Stream Recordings.
    Allows administrators to toggle whether recordings are enabled/fetched from Django Admin.
    """
    enable_recordings = models.BooleanField(
        default=False,
        help_text="If enabled, recordings will be fetched and displayed on student/teacher portals."
    )

    class Meta:
        verbose_name = "Live Session Configuration"
        verbose_name_plural = "Live Session Configuration"

    def __str__(self):
        status_text = "Enabled" if self.is_recordings_enabled else "Disabled"
        return f"Live Session Configuration (Recordings: {status_text})"

    @property
    def is_recordings_enabled(self):
        for attr in ("enable_recordings", "enable", "enabled", "is_enabled", "active"):
            if hasattr(self, attr):
                return bool(getattr(self, attr))
        return False

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)

    @classmethod
    def get_settings(cls):
        config = cls.objects.order_by("-updated_at", "-id").first()
        if not config:
            config = cls.objects.create(enable_recordings=False)
        return config


##### CREATED BY TEACHER FOR DISPLAYING PRIVATE TUTORING
class PrivateTutoring(TimeStampedModel):
    title = models.CharField(max_length=250, default="My Private Tutoring")
    teacher = models.ForeignKey("academics.TeacherProfile", on_delete=models.PROTECT, related_name="private_tutoring")
    course = models.ForeignKey("learning.Course", on_delete=models.PROTECT, related_name="private_tutoring")
    rate_per_hour = models.DecimalField(max_digits=10, decimal_places=2)
    tutoring_duration_days = models.PositiveIntegerField(default=24) # NUMBER OF DAYS THE TUTORING WILL LAST
    hours_per_day = models.FloatField(default=2.0)
    notes = models.CharField(max_length=225)
    active = models.BooleanField(default=True)


class AvailableDay(models.Model):
    class Day(models.TextChoices):
        MONDAY = "monday", "Monday"
        TUESDAY = "tuesday", "Tuesday"
        WEDNESDAY = "wednesday", "Wednesday"
        THURSDAY = "thursday", "Thursday"
        FRIDAY = "friday", "Friday"
        SATURDAY = "saturday", "Saturday"
        SUNDAY = "sunday", "Sunday"

    day = models.CharField(max_length=16, choices=Day.choices)
    private_tutoring = models.ForeignKey(PrivateTutoring, on_delete=models.PROTECT, related_name="available_days")


class TutoringBooking(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        CONFIRMED = "confirmed", "Confirmed"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"
    private_tutoring = models.ForeignKey(PrivateTutoring, on_delete=models.CASCADE, related_name="tutoring_bookings",
    blank=True, null=True)
    teacher = models.ForeignKey("academics.TeacherProfile", on_delete=models.PROTECT, related_name="tutoring_bookings")
    student = models.ForeignKey("academics.StudentProfile", on_delete=models.PROTECT, related_name="tutoring_bookings")
    duration_hours = models.PositiveIntegerField(default=2)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    notes = models.TextField(blank=True)
    completed_date = models.DateTimeField(blank=True, null=True)
    # Daily lesson time range chosen by parent (e.g. 15:00 – 17:00)
    session_start_time = models.TimeField(blank=True, null=True)
    session_end_time = models.TimeField(blank=True, null=True)



class PrivateTutoringRating(TimeStampedModel):
    parent = models.ForeignKey("academics.ParentProfile", on_delete=models.CASCADE)
    tutoring_booking = models.OneToOneField(TutoringBooking, on_delete=models.CASCADE)
    rating = models.FloatField()
    comment = models.CharField(max_length=2500)
    is_active = models.BooleanField(default=True)

