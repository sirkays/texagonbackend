from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

import random
import string
from datetime import timedelta
from django.conf import settings
from django.utils import timezone


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("Email must be set")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")
        return self.create_user(email, password, **extra_fields)

class User(AbstractUser):
    # remove username column; email becomes the login
    username = None
    email = models.EmailField(unique=True)

    avatar = models.ImageField(upload_to="avatars/", blank=True, null=True)
    phone = models.CharField(max_length=32, blank=True)
    primary_org = models.ForeignKey(
        "orgs.Organization",
        blank=True, null=True,
        on_delete=models.SET_NULL,
        related_name="primary_users",
    )

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []  # no username, so leave empty

    objects = UserManager()



class AdminAccess(models.Model):
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        limit_choices_to=Q(is_staff=True) | Q(is_superuser=True),
    )
    organizations = models.ManyToManyField("orgs.Organization", blank=True)
    selected_organization = models.ForeignKey(
        "orgs.Organization",
        related_name="adminaccess_all",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
    )
    active = models.BooleanField(default=True)

    def clean(self):
        if self.user and not (self.user.is_staff or self.user.is_superuser):
            raise ValidationError({
                "user": "AdminAccess can only be granted to staff or superusers."
            })

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    # ✅ ADD THIS
    @classmethod
    def user_has_admin_access(cls, user):
        if not user or not user.is_authenticated:
            return False
        return cls.objects.filter(user=user, active=True).exists()




class EmailOTP(models.Model):
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="email_otps",
    )
    code = models.CharField(max_length=6)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used = models.BooleanField(default=False)

    class Meta:
        indexes = [
            models.Index(fields=["user", "code", "used"]),
        ]

    @classmethod
    def generate_code(cls, length=6) -> str:
        # numeric code, e.g. 6 digits
        return "".join(random.choices(string.digits, k=length))

    @classmethod
    def create_for_user(cls, user, minutes_valid=10):
        code = cls.generate_code()
        expires_at = timezone.now() + timedelta(minutes=minutes_valid)
        return cls.objects.create(
            user=user,
            code=code,
            expires_at=expires_at,
        )

    def is_valid(self) -> bool:
        return (not self.used) and (self.expires_at >= timezone.now())
