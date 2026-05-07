# codeide/models.py

import os
from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models import TimeStampedModel
from learning.models import Lesson
from academics.models import StudentProfile, TeacherProfile


# ---------------------------------------------------------------------------
# Folder — organize CodeFiles and CodeSnippets per student
# ---------------------------------------------------------------------------
class Folder(TimeStampedModel):
    """
    A virtual folder owned by a student. Used by the IDE to organize
    code snippets and uploaded files (CodeFile). Folders can be nested
    via the optional `parent` self-relation.

    Notes:
    - Folders are scoped to a student.
    - (student, parent, name) is unique so a student cannot have two
      sibling folders with the same name.
    """
    student = models.ForeignKey(
        StudentProfile,
        on_delete=models.CASCADE,
        related_name="folders",
    )
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="children",
    )
    name = models.CharField(max_length=128)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["student", "parent", "name"],
                name="uniq_folder_per_parent_per_student",
            )
        ]
        indexes = [
            models.Index(fields=["student", "parent"]),
        ]

    def __str__(self):
        return f"[Folder] {self.name} (student={self.student_id})"

    @property
    def path(self) -> str:
        """Return human-readable path like 'projects/site/css'."""
        parts = [self.name]
        node = self.parent
        # safety bound so a corrupted cycle can't loop forever
        for _ in range(64):
            if node is None:
                break
            parts.append(node.name)
            node = node.parent
        return "/".join(reversed(parts))


class CodeSnippet(TimeStampedModel):
    """
    Saved code drafts (student only). Not necessarily submitted.
    """
    class LANGUAGE_TYPE(models.TextChoices):
        JAVASCRIPT = "javascript", "javascript"
        HTML = "html", "html"
        CSS = "css", "css"
        PYTHON = "python", "python"
        JAVA = "java", "java"

    student = models.ForeignKey(StudentProfile, on_delete=models.CASCADE, related_name="code_snippets")
    lesson = models.ForeignKey(Lesson, on_delete=models.SET_NULL, null=True, blank=True, related_name="code_snippets")
    folder = models.ForeignKey(
        Folder,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="snippets",
    )
    title = models.CharField(max_length=255, blank=True)
    language = models.CharField(max_length=64, choices=LANGUAGE_TYPE.choices, default=LANGUAGE_TYPE.HTML)
    code_text = models.TextField()
    meta = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["student", "folder"]),
            models.Index(fields=["student", "title", "language"]),
        ]

    def __str__(self):
        return f"[Snippet] {self.student_id} {self.language} {self.created_at:%Y-%m-%d}"


class CodeSubmission(TimeStampedModel):
    """
    Submitted code by lesson (student). A teacher can grade/correct.
    """
    class LANGUAGE_TYPE(models.TextChoices):
        JAVASCRIPT = "javascript", "javascript"
        HTML = "html", "html"
        CSS = "css", "css"
        PYTHON = "python", "python"
        JAVA = "java", "java"

    class Status(models.TextChoices):
        SUBMITTED = "submitted", "Submitted"
        GRADED = "graded", "Graded"
        REVISED = "revised", "Revised"

    title = models.CharField(max_length=255, blank=True, null=True)
    student = models.ForeignKey(StudentProfile, on_delete=models.CASCADE, related_name="code_submissions")
    lesson = models.ForeignKey(Lesson, on_delete=models.CASCADE, related_name="code_submissions")

    language = models.CharField(max_length=64, choices=LANGUAGE_TYPE.choices, default=LANGUAGE_TYPE.HTML)
    file_name = models.CharField(
        max_length=255, blank=True, default="",
        help_text="Individual filename within the project (e.g. 'index.html', 'style.css').",
    )
    code_text = models.TextField()

    status = models.CharField(max_length=16, choices=Status.choices, default=Status.SUBMITTED)

    # grading / corrections
    score = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    feedback = models.TextField(blank=True)
    correction_code = models.TextField(blank=True)
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


def codefile_upload_to(instance, filename: str) -> str:
    from datetime import datetime
    today = datetime.utcnow()
    safe_name = os.path.basename(filename)
    return f"texagon/codefiles/{instance.student_id}/{today:%Y/%m/%d}/{safe_name}"


class CodeFile(TimeStampedModel):
    """
    Binary/text file a student can upload for use in the code IDE.
    """
    student = models.ForeignKey(
        StudentProfile, on_delete=models.CASCADE, related_name="code_files"
    )
    lesson = models.ForeignKey(
        Lesson, on_delete=models.SET_NULL, null=True, blank=True, related_name="code_files"
    )
    folder = models.ForeignKey(
        Folder,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="files",
    )
    label = models.CharField(max_length=255, blank=True)
    file = models.FileField(upload_to=codefile_upload_to, max_length=512)
    original_name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=127, blank=True)
    size_bytes = models.BigIntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["lesson", "student"]),
            models.Index(fields=["student", "folder"]),
        ]

    def __str__(self):
        return f"[CodeFile] student={self.student_id} name={self.original_name}"

    @property
    def url(self) -> str:
        try:
            return self.file.url
        except Exception:
            return ""