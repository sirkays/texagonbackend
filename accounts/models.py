from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

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
    # Only allow staff or superusers to be selectable in forms/admin:
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        limit_choices_to=Q(is_staff=True) | Q(is_superuser=True),
    )
    organizations = models.ManyToManyField("orgs.Organization", blank=True)
    selected_organization = models.ForeignKey("orgs.Organization", 
    related_name="adminaccess_all", on_delete=models.CASCADE, blank=True, null=True)
    active = models.BooleanField(default=True)

    def clean(self):
        # Enforce at the model level (covers shell, scripts, fixtures, etc.)
        if self.user and not (self.user.is_staff or self.user.is_superuser):
            raise ValidationError({
                "user": "AdminAccess can only be granted to staff or superusers."
            })

    def save(self, *args, **kwargs):
        # Ensure clean() runs on every save
        self.full_clean()
        return super().save(*args, **kwargs)
