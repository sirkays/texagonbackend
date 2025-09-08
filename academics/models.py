from django.db import models
from django.conf import settings
from core.models import TimeStampedModel, NamedModel

class Classroom(NamedModel):
    organization = models.ForeignKey("orgs.Organization", on_delete=models.CASCADE, related_name="classrooms")
    code = models.CharField(max_length=32, blank=True)
    teachers = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name="teaching_classrooms", blank=True)

    class Meta:
        unique_together = ("organization", "name")

class Subject(NamedModel):
    organization = models.ForeignKey("orgs.Organization", on_delete=models.CASCADE, related_name="subjects")
    code = models.CharField(max_length=32, blank=True)

    class Meta:
        unique_together = ("organization", "name")

class StudentProfile(TimeStampedModel):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="student_profile")
    organization = models.ForeignKey("orgs.Organization", on_delete=models.CASCADE, related_name="students")
    current_classroom = models.ForeignKey(Classroom, on_delete=models.SET_NULL, null=True, blank=True)
    admission_no = models.CharField(max_length=64, blank=True)
    dob = models.DateField(null=True, blank=True)

    def __str__(self):
        return f"Student: {self.user.get_full_name() or self.user.username}"

class TeacherProfile(TimeStampedModel):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="teacher_profile")
    organization = models.ForeignKey("orgs.Organization", on_delete=models.CASCADE, related_name="teachers")
    bio = models.TextField(blank=True)
    specialties = models.ManyToManyField(Subject, blank=True)

    def __str__(self):
        return f"Teacher: {self.user.get_full_name() or self.user.username} Email: {self.user.email}"

class ParentProfile(TimeStampedModel):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="parent_profile")
    organization = models.ForeignKey("orgs.Organization", on_delete=models.CASCADE, related_name="parents")
    address = models.TextField(blank=True)

    def __str__(self):
        return f"Parent: {self.user.get_full_name() or self.user.username}"

class ParentChildLink(TimeStampedModel):
    parent = models.ForeignKey(ParentProfile, on_delete=models.CASCADE, related_name="children_links")
    student = models.ForeignKey(StudentProfile, on_delete=models.CASCADE, related_name="parent_links")
    relationship = models.CharField(max_length=64, blank=True)

    class Meta:
        unique_together = ("parent", "student")
