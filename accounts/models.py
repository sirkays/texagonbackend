from django.contrib.auth.models import AbstractUser
from django.db import models

class User(AbstractUser):
    avatar = models.ImageField(upload_to="avatars/", blank=True, null=True)
    phone = models.CharField(max_length=32, blank=True)
    # Keep primary_org lightweight to avoid deps; use string ref
    primary_org = models.ForeignKey(
        "orgs.Organization", blank=True, null=True, on_delete=models.SET_NULL, related_name="primary_users"
    )
