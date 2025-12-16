# achievements/models.py
from django.db import models
from django.utils import timezone
from core.models import TimeStampedModel, NamedModel
from orgs.models import Organization
from gamification.services.streaks import build_streak


class ActivityEvent(TimeStampedModel):
    """
    Generic event log for gamification.

    You will record these events whenever a student does something:
    - exercise_solved
    - quiz_attempted
    - quiz_passed
    - course_completed
    - daily_active
    - time_spent_minutes
    etc.

    Achievements are computed from these events using JSON rules.
    """
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="activity_events"
    )
    student = models.ForeignKey(
        "academics.StudentProfile",
        on_delete=models.CASCADE,
        related_name="activity_events",
    )

    event_type = models.CharField(max_length=64, db_index=True)
    value = models.IntegerField(default=1)  # score, minutes, count=1, etc
    meta = models.JSONField(default=dict, blank=True)
    dedupe_key = models.CharField(max_length=128, unique=True, db_index=True, blank=True, null=True)
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=["organization", "event_type", "occurred_at"]),
            models.Index(fields=["student", "event_type", "occurred_at"]),
        ]

    def __str__(self):
        return f"{self.event_type} ({self.student_id})"


class AchievementDefinition(TimeStampedModel):
    """
    Fully dynamic achievement definition.
    Admin can add NEW achievements by creating rows with `rule` JSON.

    code: stable key for frontend / API.
    rule: describes how to compute progress from ActivityEvent.
    """
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="achievement_defs",
        null=True,
        blank=True,
    )

    code = models.CharField(max_length=64)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    icon = models.CharField(max_length=64, default="star")
    category = models.CharField(max_length=64, default="General")
    points = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    # Fully dynamic rule (admin-editable)
    # Examples are shown below.
    rule = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["code"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "code"],
                name="uniq_org_achievement_code",
            )
        ]

    def __str__(self):
        org = self.organization_id or "GLOBAL"
        return f"{org}:{self.code} ({self.title})"


class AchievementAcquired(TimeStampedModel):
    """
    Created automatically when an achievement is unlocked.
    """
    definition = models.ForeignKey(
        AchievementDefinition,
        on_delete=models.CASCADE,
        related_name="acquired",
    )
    student = models.ForeignKey(
        "academics.StudentProfile",
        on_delete=models.CASCADE,
        related_name="achievements_acquired",
    )
    acquired_at = models.DateTimeField(default=timezone.now)

    # store computed progress at time of unlock (useful for debugging)
    value_at_unlock = models.IntegerField(default=0)
    meta = models.JSONField(default=dict, blank=True)

    class Meta:
        unique_together = ("definition", "student")
        indexes = [
            models.Index(fields=["student", "definition"]),
            models.Index(fields=["definition", "acquired_at"]),
        ]

    def __str__(self):
        return f"{self.student_id} → {self.definition.code}"


class Badge(NamedModel):
    """
    Dynamic badges based on points threshold.
    Admin can add new badges anytime.
    """
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="badges",
        blank=True,
        null=True,
    )
    icon_name = models.CharField(max_length=64, default="medal")
    color = models.CharField(max_length=64, default="bg-gray-400")
    points = models.PositiveIntegerField(default=0)  # threshold
    criteria = models.TextField(blank=True)
    rules = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = "Badge"
        verbose_name_plural = "Badges"
        indexes = [models.Index(fields=["organization", "points"])]

    def __str__(self):
        return self.name


class BadgeAward(TimeStampedModel):
    badge = models.ForeignKey(Badge, on_delete=models.CASCADE, related_name="awards")
    student = models.ForeignKey(
        "academics.StudentProfile",
        on_delete=models.CASCADE,
        related_name="badge_awards",
    )
    awarded_at = models.DateTimeField(auto_now_add=True)
    reason = models.CharField(max_length=255, blank=True)

    class Meta:
        unique_together = ("badge", "student")
        indexes = [models.Index(fields=["student", "badge"])]


class PointTransaction(TimeStampedModel):
    """
    A ledger of points. (Recommended)
    """
    student = models.ForeignKey(
        "academics.StudentProfile",
        on_delete=models.CASCADE,
        related_name="point_transactions",
    )
    points = models.IntegerField()
    reason = models.CharField(max_length=255, blank=True)
    balance_after = models.IntegerField(default=0)

    class Meta:
        indexes = [models.Index(fields=["student", "created_at"])]


class Streak(TimeStampedModel):
    """
    Optional: keep streak calculated from daily activity,
    or update it directly when logging 'daily_active' events.
    """
    student = models.OneToOneField(
        "academics.StudentProfile",
        on_delete=models.CASCADE,
        related_name="streak",
    )
    current_days = models.PositiveIntegerField(default=0)
    longest_days = models.PositiveIntegerField(default=0)
    last_activity = models.DateField(null=True, blank=True)

    @classmethod
    def set_student_streak(cls, student, org, event_type, code):
        ev_qs = ActivityEvent.objects.filter(
            student=student,
            organization=org,
            event_type=event_type,
        )
        if event_type == "daily_active" and code == "streak_champion":
            total = build_streak(ev_qs).count()
            streak, is_created = Streak.objects.update_or_create(
                student=student,
                defaults={
                    "current_days":total,
                    "last_activity":timezone.localdate()
                }
            )

            if streak.longest_days < total:
                streak.longest_days = total
                streak.save()

