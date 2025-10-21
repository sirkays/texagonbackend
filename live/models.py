from django.db import models
from core.models import TimeStampedModel
from django.utils import timezone

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



class PrivateTutoring(TimeStampedModel):
    teacher = models.ForeignKey("academics.TeacherProfile", on_delete=models.PROTECT, related_name="private_tutoring")
    course = models.ForeignKey("learning.Course", on_delete=models.PROTECT, related_name="private_tutoring")
    rate_per_hour = models.DecimalField(max_digits=10, decimal_places=2)
    tutoring_duration_days = models.PositiveIntegerField(default=24) # NUMBER OF DAYS THE TUTORING WILL LAST
    notes = models.CharField(max_length=225)
    

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





