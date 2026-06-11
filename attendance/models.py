from django.db import models
from core.models import TimeStampedModel

class AttendanceSession(TimeStampedModel):
    course = models.ForeignKey("learning.Course", on_delete=models.CASCADE, related_name="attendance_sessions")
    academic_session = models.ForeignKey(
        "orgs.AcademicSession",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="attendance_sessions"
    )
    date = models.DateField()
    topic = models.CharField(max_length=255, blank=True)

    class Meta:
        unique_together = ("course", "date")
        ordering = ["-date"]

class AttendanceRecord(TimeStampedModel):
    session = models.ForeignKey(AttendanceSession, on_delete=models.CASCADE, related_name="records")
    student = models.ForeignKey("academics.StudentProfile", on_delete=models.CASCADE, related_name="attendance_records")
    present = models.BooleanField(default=True)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        unique_together = ("session", "student")
