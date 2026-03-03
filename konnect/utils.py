from texagonbackend.settings import KONNECT_MAX_ROOM
from .models import KonnectRoom
from django.utils import timezone

def check_room(konn3ct):
    data = konn3ct.list_rooms()
    if data.get('success'):
        if len(data.get('data')) < KONNECT_MAX_ROOM:
            return None, True
        
        oldest = KonnectRoom.oldest_room()
        return oldest, False

    return None, False


def save_last_update(konnect_room):
    konnect_room.last_update = timezone.now()
    konnect_room.save()