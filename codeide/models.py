# app: ide (or wherever you keep learning-related APIs)
from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models import TimeStampedModel
from learning.models import Lesson
from academics.models import StudentProfile, TeacherProfile


class CodeSnippet(TimeStampedModel):
    """
    Saved code drafts (student only). Not necessarily submitted.
    """
    student = models.ForeignKey(StudentProfile, on_delete=models.CASCADE, related_name="code_snippets")
    lesson = models.ForeignKey(Lesson, on_delete=models.SET_NULL, null=True, blank=True, related_name="code_snippets")
    title = models.CharField(max_length=255, blank=True)
    language = models.CharField(max_length=64)  # e.g. 'python', 'javascript', 'java', ...
    code_text = models.TextField()
    meta = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"[Snippet] {self.student_id} {self.language} {self.created_at:%Y-%m-%d}"


class CodeSubmission(TimeStampedModel):
    """
    Submitted code by lesson (student). A teacher can grade/correct.
    """
    class Status(models.TextChoices):
        SUBMITTED = "submitted", "Submitted"
        GRADED = "graded", "Graded"
        REVISED = "revised", "Revised"

    student = models.ForeignKey(StudentProfile, on_delete=models.CASCADE, related_name="code_submissions")
    lesson = models.ForeignKey(Lesson, on_delete=models.CASCADE, related_name="code_submissions")

    language = models.CharField(max_length=64)
    code_text = models.TextField()

    status = models.CharField(max_length=16, choices=Status.choices, default=Status.SUBMITTED)

    # grading / corrections
    score = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    feedback = models.TextField(blank=True)
    correction_code = models.TextField(blank=True)  # teacher-proposed corrected solution
    graded_by = models.ForeignKey(TeacherProfile, on_delete=models.SET_NULL, null=True, blank=True, related_name="graded_code_submissions")
    graded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["lesson", "student"])]

    def __str__(self):
        return f"[Submission] lesson={self.lesson_id} student={self.student_id} status={self.status}"


class CodeComment(TimeStampedModel):
    """
    Threaded discussion on a submission. Both student and teacher can post.
    """
    submission = models.ForeignKey(CodeSubmission, on_delete=models.CASCADE, related_name="comments")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="code_comments")
    author_role = models.CharField(max_length=16, choices=[("student", "Student"), ("teacher", "Teacher")])
    message = models.TextField()

    class Meta:
        ordering = ["created_at"]
