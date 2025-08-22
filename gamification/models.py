from django.db import models
from core.models import TimeStampedModel, NamedModel

class Badge(NamedModel):
    organization = models.ForeignKey("orgs.Organization", on_delete=models.CASCADE, related_name="badges")
    icon = models.ImageField(upload_to="badges/", blank=True, null=True)
    criteria = models.TextField(blank=True)
    rules = models.JSONField(default=dict, blank=True)

class BadgeAward(TimeStampedModel):
    badge = models.ForeignKey(Badge, on_delete=models.CASCADE, related_name="awards")
    student = models.ForeignKey("academics.StudentProfile", on_delete=models.CASCADE, related_name="badge_awards")
    awarded_at = models.DateTimeField(auto_now_add=True)
    reason = models.CharField(max_length=255, blank=True)

    class Meta:
        unique_together = ("badge", "student", "awarded_at")

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
