from django.db import models
from django.utils import timezone
from core.models import TimeStampedModel
from decimal import Decimal
from django.db.models import (
    Sum,
)

class Test(TimeStampedModel):
    class Visibility(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"
        CLOSED = "closed", "Closed"

    # ✅ NEW
    class Mode(models.TextChoices):
        ONLINE = "online", "Online"
        OFFLINE = "offline", "Offline"

    course = models.ForeignKey("learning.Course", on_delete=models.CASCADE, related_name="tests")
    title = models.CharField(max_length=255)
    duration_minutes = models.PositiveIntegerField(default=30)
    total_marks = models.DecimalField(max_digits=7, decimal_places=2, default=0)
    visibility = models.CharField(max_length=16, choices=Visibility.choices, default=Visibility.DRAFT)

    # ✅ NEW (teacher sets this)
    mode = models.CharField(max_length=16, choices=Mode.choices, default=Mode.ONLINE, db_index=True)

    instructions = models.TextField(blank=True)
    start_at = models.DateTimeField(null=True, blank=True)
    end_at = models.DateTimeField(null=True, blank=True)
    settings = models.JSONField(default=dict, blank=True)
    excluded_users = models.ManyToManyField("academics.StudentProfile",blank=True)
    require_browser_code = models.BooleanField(default=False)

    def __str__(self):
        return self.title


    def update_total_marks(self):
        total = self.questions.aggregate(
            total=Sum("points")
        )["total"] or 0
        self.total_marks = total
        self.save(update_fields=["total_marks"])
        

class Question(TimeStampedModel):
    class Type(models.TextChoices):
        SCQ = "scq", "Single Choice"
        MCQ = "mcq", "Multiple Choice"
        TRUE_FALSE = "tf", "True/False"
        SHORT = "short", "Short Answer"
        ESSAY = "essay", "Essay"

    test = models.ForeignKey(Test, on_delete=models.CASCADE, related_name="questions")
    order = models.PositiveIntegerField(default=1)
    qtype = models.CharField(max_length=8, choices=Type.choices)
    body = models.TextField()
    points = models.DecimalField(max_digits=6, decimal_places=2, default=1)
    meta = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["order"]
        unique_together = ("test", "order")

class Choice(TimeStampedModel):
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="choices")
    order = models.PositiveIntegerField(default=1)
    text = models.TextField()
    is_correct = models.BooleanField(default=False)

    class Meta:
        ordering = ["order"]
        unique_together = ("question", "order")

class TestAttempt(TimeStampedModel):
    test = models.ForeignKey(Test, on_delete=models.CASCADE, related_name="attempts")
    student = models.ForeignKey("academics.StudentProfile", on_delete=models.CASCADE, related_name="test_attempts")
    started_at = models.DateTimeField(default=timezone.now)
    submitted_at = models.DateTimeField(null=True, blank=True)
    score = models.DecimalField(max_digits=7, decimal_places=2, default=0)
    answers = models.JSONField(default=dict, blank=True)  # {question_id: ["A", ...] or "True"/"text"}
    status = models.CharField(max_length=16, default="in_progress")  # in_progress, submitted, graded

    class Meta:
        unique_together = ("test", "student", "started_at")

class Assignment(TimeStampedModel):
    course = models.ForeignKey("learning.Course", on_delete=models.CASCADE, related_name="assignments")
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    due_at = models.DateTimeField(null=True, blank=True)
    attachments = models.JSONField(default=list, blank=True)

class Submission(TimeStampedModel):
    assignment = models.ForeignKey(Assignment, on_delete=models.CASCADE, related_name="submissions")
    student = models.ForeignKey("academics.StudentProfile", on_delete=models.CASCADE, related_name="submissions")
    text = models.TextField(blank=True)
    file = models.FileField(upload_to="assignments/submissions/", blank=True, null=True)
    submitted_at = models.DateTimeField(default=timezone.now)
    score = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    feedback = models.TextField(blank=True)

    class Meta:
        unique_together = ("assignment", "student")



class TestAnswer(TimeStampedModel):
    """
    One row per question answered inside a TestAttempt.
    Supports:
      - SCQ/TF: selected_choice
      - MCQ: selected_choice_ids (list of choice ids)
      - SHORT/ESSAY: answer_text
    Also records awarded_points and whether it was auto-graded.
    """
    attempt = models.ForeignKey(TestAttempt, on_delete=models.CASCADE, related_name="answers_rows")
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="answers")
    # For SCQ/TF
    selected_choice = models.ForeignKey(Choice, on_delete=models.SET_NULL, null=True, blank=True, related_name="selected_in_answers")
    # For MCQ: store all selected choice IDs (to avoid a join table)
    selected_choice_ids = models.JSONField(default=list, blank=True)
    # For text questions
    answer_text = models.TextField(blank=True)
    # Grading
    awarded_points = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("0"))
    is_auto_graded = models.BooleanField(default=False)

    class Meta:
        unique_together = ("attempt", "question")
        indexes = [
            models.Index(fields=["attempt"]),
            models.Index(fields=["question"]),
        ]

    def __str__(self):
        return f"Answer attempt={self.attempt_id} q={self.question_id}"