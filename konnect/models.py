from django.db import models
from core.models import NamedModel
from accounts.models import User
from learning.models import Course
from django.db.models import F
from django.core.exceptions import ObjectDoesNotExist
from django.utils import timezone
from datetime import timedelta

class KonnectRoom(NamedModel):
    class STAT(models.TextChoices):
        CLOSED = "closed", "closed"
        DISABLED = "disabled", "disabled"
        OPEN = "open", "open"

    creator = models.ForeignKey(User, on_delete=models.CASCADE, related_name='konnect_rooms')
    room_id = models.CharField(max_length=255)
    welcome_message = models.CharField(max_length=1200)
    message = models.CharField(max_length=1200)
    room_url = models.URLField(
        max_length=1200,
        blank=True,
        null=True,
        unique=True
    )
    status = models.CharField(max_length=16, choices=STAT.choices, default=STAT.CLOSED)
    allowed_courses = models.ManyToManyField(Course, blank=True)
    allowed_users = models.ManyToManyField(User, blank=True)
    last_update = models.DateTimeField(blank=True, null=True)

    @classmethod
    def oldest_room(cls):
        """
        Returns the KonnectRoom object with the earliest (oldest) last_update
        that is at least 12 hours old (i.e., last_update <= now - 12 hours).
        If no such room exists, returns None.
        """
        threshold = timezone.now() - timedelta(hours=12)
        return cls.objects.filter(
            last_update__isnull=False,
            last_update__lte=threshold
        ).order_by('last_update').first()

class KonnectRoomUser(NamedModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='konnect_room_users')
    konnect_room = models.ForeignKey(KonnectRoom, on_delete=models.CASCADE, related_name="konnect_users")
    active = models.BooleanField(default=False)


