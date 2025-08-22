from django.db import models
from django.utils import timezone
from core.models import TimeStampedModel

class Test(TimeStampedModel):
    class Visibility(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"
        CLOSED = "closed", "Closed"

    course = models.ForeignKey("learning.Course", on_delete=models.CASCADE, related_name="tests")
    title = models.CharField(max_length=255)
    duration_minutes = models.PositiveIntegerField(default=30)
    total_marks = models.DecimalField(max_digits=7, decimal_places=2, default=0)
    visibility = models.CharField(max_length=16, choices=Visibility.choices, default=Visibility.DRAFT)
    instructions = models.TextField(blank=True)
    start_at = models.DateTimeField(null=True, blank=True)
    end_at = models.DateTimeField(null=True, blank=True)
    settings = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return self.title

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
