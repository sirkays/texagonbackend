from django.db import models
from django.conf import settings
from core.models import TimeStampedModel, NamedModel
from live.models import TutoringBooking

class Course(NamedModel):
    USAGE_CHOICE = (
        ('public','public'),
        ('private','private'),
    )
    organization = models.ForeignKey("orgs.Organization", on_delete=models.CASCADE, related_name="courses")
    subject = models.ForeignKey("academics.Subject", on_delete=models.PROTECT)
    classroom = models.ForeignKey("academics.Classroom", on_delete=models.PROTECT)
    teacher = models.ForeignKey("academics.TeacherProfile", on_delete=models.PROTECT, related_name="courses")
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    usage_type = models.CharField(
        max_length=20,
        choices=USAGE_CHOICE,
        default='public',
    )
    private_tutor = models.ForeignKey(TutoringBooking, on_delete=models.CASCADE, blank=True, null=True)

    def __str__(self):
        return self.teacher.user.email

    class Meta:
        unique_together = ("organization", "subject", "classroom", "teacher")

class Enrollment(TimeStampedModel):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        COMPLETED = "completed", "Completed"
        DROPPED = "dropped", "Dropped"

    student = models.ForeignKey("academics.StudentProfile", on_delete=models.CASCADE, related_name="enrollments")
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="enrollments")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    progress_pct = models.DecimalField(max_digits=5, decimal_places=2, default=0)

    class Meta:
        unique_together = ("student", "course")

class ModuleCategory(NamedModel):
    active = models.BooleanField(default=True)

class Module(NamedModel):
    class DifficultyLevel(models.TextChoices):
        BEGINNER = "BEGINNER", "BEGINNER"
        INTERMEDIATE = "INTERMEDIATE", "INTERMEDIATE"
        ADVANCED = "ADVANCED", "ADVANCED"
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="modules")
    order = models.PositiveIntegerField(default=1)
    description = models.TextField(blank=True, null=True)
    difficulty = models.CharField(max_length=25, choices=DifficultyLevel.choices, default=DifficultyLevel.BEGINNER)
    category = models.ForeignKey(ModuleCategory, on_delete=models.CASCADE,  blank=True, null=True)
    estimated_duration_in_minutes = models.PositiveIntegerField(blank=True, null=True) 
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["order"]
        unique_together = ("course", "order")

class Lesson(NamedModel):
    class ContentType(models.TextChoices):
        VIDEO = "video", "Video"
        AUDIO = "audio", "Audio"
        PDF = "pdf", "PDF"
        DOC = "doc", "Document"
        LINK = "link", "External Link"

    module = models.ForeignKey(Module, on_delete=models.CASCADE, related_name="lessons")
    cover_image = models.ImageField(upload_to="covers/", blank=True, null=True)
    order = models.PositiveIntegerField(default=1)
    content_type = models.CharField(max_length=16, choices=ContentType.choices)
    file = models.FileField(upload_to="lessons/files/", blank=True, null=True)
    url = models.URLField(blank=True)
    duration_seconds = models.PositiveIntegerField(default=0)
    meta = models.JSONField(default=dict, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["order"]
        unique_together = ("module", "order")

class Material(TimeStampedModel):
    class Kind(models.TextChoices):
        VIDEO = "video", "Video"
        AUDIO = "audio", "Audio"
        PDF = "pdf", "PDF"
        DOC = "doc", "Document"
        IMAGE = "image", "Image"
        OTHER = "other", "Other"

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="materials")
    organization = models.ForeignKey("orgs.Organization", on_delete=models.CASCADE, related_name="materials")
    title = models.CharField(max_length=255)
    kind = models.CharField(max_length=16, choices=Kind.choices)
    file = models.FileField(upload_to="materials/files/", blank=True, null=True)
    url = models.URLField(blank=True)
    tags = models.JSONField(default=list, blank=True)
    is_public = models.BooleanField(default=False)
    active = models.BooleanField(default=True)

    def __str__(self):
        return self.title

class Bookmark(TimeStampedModel):
    student = models.ForeignKey("academics.StudentProfile", on_delete=models.CASCADE, related_name="bookmarks")
    lesson = models.ForeignKey(Lesson, on_delete=models.CASCADE, related_name="bookmarks")
    note = models.CharField(max_length=255, blank=True)
    position_seconds = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ("student", "lesson", "position_seconds")

class Note(TimeStampedModel):
    student = models.ForeignKey("academics.StudentProfile", on_delete=models.CASCADE, related_name="notes")
    lesson = models.ForeignKey(Lesson, on_delete=models.CASCADE, related_name="notes")
    content = models.TextField()
    is_private = models.BooleanField(default=True)