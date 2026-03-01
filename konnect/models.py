from django.db import models
from core.models import NamedModel
from accounts.models import User
from learning.models import Course


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
        max_length=1200,      # Default is 200
        blank=True,          # Optional in forms
        null=True,           # Optional in database
        unique=True          # Must be unique
    )
    status = models.CharField(max_length=16, choices=STAT.choices, default=STAT.CLOSED)
    allowed_courses = models.ManyToManyField(Course, blank=True)
    allowed_users = models.ManyToManyField(User, blank=True)



class KonnectRoomUser(NamedModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='konnect_room_users')
    konnect_room = models.ForeignKey(KonnectRoom, on_delete=models.CASCADE, related_name="konnect_users")
    active = models.BooleanField(default=False)


