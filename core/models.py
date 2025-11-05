from django.db import models
from django.utils import timezone
from django.conf import settings

class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True

class NamedModel(TimeStampedModel):
    name = models.CharField(max_length=255, db_index=True)

    class Meta:
        abstract = True

    def __str__(self) -> str:
        return self.name



class StudentDevice(models.Model):
    student = models.ForeignKey('academics.StudentProfile', on_delete=models.CASCADE, related_name='devices')
    device_id = models.CharField(max_length=64, unique=True)
    user_agent = models.TextField(blank=True)
    ip_hash = models.CharField(max_length=64, blank=True)
    first_seen = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["student", "device_id"]),
        ]

    def __str__(self):
        return f"{self.student_id}:{self.device_id[:8]}"
