from django.db import models
from django.utils import timezone
from django.conf import settings
from django.core.validators import MinValueValidator

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



class Tier(models.Model):
    """
    Represents a level/tier threshold.
    Example:
      threshold_xp=0     name="Newbie"
      threshold_xp=2500  name="Bronze Beginner"
      ...
    """
    name = models.CharField(max_length=80, unique=True)
    threshold_xp = models.PositiveIntegerField(
        unique=True,
        validators=[MinValueValidator(0)],
        help_text="Minimum XP required to be in this tier."
    )
    order = models.PositiveSmallIntegerField(
        default=0,
        help_text="Optional explicit ordering if you ever want it independent of threshold."
    )

    class Meta:
        ordering = ["threshold_xp"]  # important for 'next tier' logic

    def __str__(self) -> str:
        return f"{self.name} (>= {self.threshold_xp} XP)"


class TieringService:
    """
    Helper you can call from anywhere (views/serializers/model methods).
    """
    @staticmethod
    def level_for_xp(xp: int) -> dict:
        xp = max(int(xp or 0), 0)

        # Current tier = highest threshold <= xp
        current = (
            Tier.objects
            .filter(threshold_xp__lte=xp)
            .order_by("-threshold_xp")
            .first()
        )

        # If DB empty, fallback
        if not current:
            return {
                "level_name": "Newbie",
                "next_threshold": None,
                "xp_to_next": 0,
                "progress_to_next_pct": 100,
            }

        # Next tier = smallest threshold > xp
        nxt = (
            Tier.objects
            .filter(threshold_xp__gt=current.threshold_xp)
            .order_by("threshold_xp")
            .first()
        )

        next_threshold = nxt.threshold_xp if nxt else None
        xp_to_next = max(next_threshold - xp, 0) if next_threshold is not None else 0

        floor = current.threshold_xp
        if next_threshold is None:
            pct = 100
        else:
            span = max(next_threshold - floor, 1)
            pct = int(((xp - floor) / span) * 100)

        return {
            "level_name": current.name,
            "next_threshold": next_threshold,
            "xp_to_next": xp_to_next,
            "progress_to_next_pct": pct,
        }
