from django.db import models
from django.utils import timezone

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
