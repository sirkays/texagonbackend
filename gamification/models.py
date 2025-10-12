
# achievements/models.py (or inside your existing composite models file)
from django.db import models
from django.conf import settings
from core.models import TimeStampedModel, NamedModel
from orgs.models import Organization

class AchievementDefinition(TimeStampedModel):
    """
    DB-driven configuration for each achievement tile in the UI.
    code: a stable key your backend uses to compute progress.
    target_value: the numeric goal (e.g. 30 days streak, 3 courses, 10 exercises, 5 quizzes @≥90%).
    points: how many points the student gets for unlocking this achievement (UI display + award logic if you add).
    """
    class Code(models.TextChoices):
        FIRST_STEPS       = "first_steps", "First Steps"
        CODE_WARRIOR      = "code_warrior", "Code Warrior"
        QUIZ_MASTER       = "quiz_master", "Quiz Master"
        STREAK_CHAMPION   = "streak_champion", "Streak Champion"
        COURSE_CONQUEROR  = "course_conqueror", "Course Conqueror"

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="achievement_defs", null=True, blank=True)
    code = models.CharField(max_length=64, choices=Code.choices, unique=True)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    icon = models.CharField(max_length=64, default="star")      # e.g. 'trophy', 'target', 'zap'
    category = models.CharField(max_length=64, default="General")
    target_value = models.PositiveIntegerField(null=True, blank=True)  # the numeric goal; NULL for achievements without a numeric target
    points = models.PositiveIntegerField(default=0)             # points shown for this achievement
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} ({self.title})"


# Expand your Badge to carry UI + threshold info (keep existing rows compatible).
# If you already have a second Badge class, merge them and run a migration.
class Badge(NamedModel):
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="badges")
    # keep existing file/icon if you like; we add string-based icon/color for your UI:
    icon_name = models.CharField(max_length=64, default="medal")     # e.g. 'gem', 'crown', 'trophy'
    color = models.CharField(max_length=64, default="bg-gray-400")   # Tailwind class used in UI
    points = models.PositiveIntegerField(default=0)                   # points threshold (for progress bar)
    criteria = models.TextField(blank=True)
    rules = models.JSONField(default=dict, blank=True)               # optional, if you want rule JSON

    class Meta:
        verbose_name = "Badge"
        verbose_name_plural = "Badges"
        indexes = [models.Index(fields=["organization", "points"])]

    def __str__(self):
        return self.name


class BadgeAward(TimeStampedModel):
    badge = models.ForeignKey(Badge, on_delete=models.CASCADE, related_name="awards")
    student = models.ForeignKey("academics.StudentProfile", on_delete=models.CASCADE, related_name="badge_awards")
    awarded_at = models.DateTimeField(auto_now_add=True)
    reason = models.CharField(max_length=255, blank=True)

    class Meta:
        unique_together = ("badge", "student")
        indexes = [models.Index(fields=["student", "badge"])]


class PointTransaction(TimeStampedModel):
    student = models.ForeignKey("academics.StudentProfile", on_delete=models.CASCADE, related_name="point_transactions")
    points = models.IntegerField()
    reason = models.CharField(max_length=255, blank=True)
    balance_after = models.IntegerField(default=0)

class Streak(TimeStampedModel):
    student = models.OneToOneField("academics.StudentProfile", on_delete=models.CASCADE, related_name="streak")
    current_days = models.PositiveIntegerField(default=0)
    longest_days = models.PositiveIntegerField(default=0)
    last_activity = models.DateField(null=True, blank=True)

