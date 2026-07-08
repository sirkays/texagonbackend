from django.db import models
from django.utils import timezone
from core.models import TimeStampedModel
from decimal import Decimal


class OfflinePracticalWork(TimeStampedModel):
    """
    Represents an Assignment or Assessment conducted OUTSIDE the LMS
    (e.g. a physical exam, a field project, a classroom test on paper).
    The teacher creates this record to log it inside the LMS and enter scores.
    """

    class AssessmentType(models.TextChoices):
        ASSIGNMENT = "assignment", "Assignment"
        ASSESSMENT = "assessment", "Assessment"
        EXAM = "exam", "Exam"
        QUIZ = "quiz", "Quiz"
        PROJECT = "project", "Project"
        PRACTICAL = "practical", "Practical"

    class Visibility(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"

    # Core relations
    course = models.ForeignKey(
        "learning.Course",
        on_delete=models.CASCADE,
        related_name="offline_practical_works",
    )
    academic_session = models.ForeignKey(
        "orgs.AcademicSession",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="offline_practical_works",
    )
    created_by = models.ForeignKey(
        "academics.TeacherProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="offline_practical_works",
    )

    # Metadata
    title = models.CharField(max_length=255)
    assessment_type = models.CharField(
        max_length=16,
        choices=AssessmentType.choices,
        default=AssessmentType.ASSIGNMENT,
        db_index=True,
    )
    max_score = models.DecimalField(
        max_digits=7,
        decimal_places=2,
        default=Decimal("100.00"),
    )
    conducted_at = models.DateField(null=True, blank=True)
    description = models.TextField(blank=True)
    visibility = models.CharField(
        max_length=16,
        choices=Visibility.choices,
        default=Visibility.PUBLISHED,
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Off-Practical Work"
        verbose_name_plural = "Off-Practical Works"

    def __str__(self):
        return f"{self.title} ({self.get_assessment_type_display()})"

    # ── Computed helpers ──────────────────────────────────────────────────
    @property
    def score_count(self):
        return self.scores.count()

    @property
    def graded_count(self):
        return self.scores.filter(score__isnull=False).count()

    @property
    def pending_count(self):
        return self.scores.filter(score__isnull=True).count()

    @property
    def average_score(self):
        from django.db.models import Avg
        result = self.scores.filter(score__isnull=False).aggregate(avg=Avg("score"))
        avg = result.get("avg")
        if avg is None:
            return None
        return round(float(avg), 2)


class OfflinePracticalScore(TimeStampedModel):
    """
    One score record per student per OfflinePracticalWork.
    Created/updated by the teacher when entering scores.
    """

    opw = models.ForeignKey(
        OfflinePracticalWork,
        on_delete=models.CASCADE,
        related_name="scores",
    )
    student = models.ForeignKey(
        "academics.StudentProfile",
        on_delete=models.CASCADE,
        related_name="offline_practical_scores",
    )
    score = models.DecimalField(
        max_digits=7,
        decimal_places=2,
        null=True,
        blank=True,
    )
    feedback = models.TextField(blank=True)
    recorded_at = models.DateTimeField(default=timezone.now)
    recorded_by = models.ForeignKey(
        "academics.TeacherProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recorded_offline_scores",
    )

    class Meta:
        unique_together = ("opw", "student")
        ordering = ["student__user__first_name", "student__user__last_name"]
        verbose_name = "OPW Score"
        verbose_name_plural = "OPW Scores"

    def __str__(self):
        return f"{self.opw.title} — {self.student} — {self.score}"
