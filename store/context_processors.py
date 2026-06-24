from store.views import _get_or_create_cart


def cart_item_count(request):
    """
    Injects `cart_item_count` into every template context so the header
    badge always reflects the current session/user cart.
    """
    try:
        cart = _get_or_create_cart(request)
        count = cart.items.count()
    except Exception:
        count = 0
    return {'cart_item_count': count}


def unread_notifications_count(request):
    """
    Injects `unread_notifications_count` into every template context so the header
    badge always reflects the current user's unread notifications.
    """
    if request.user.is_authenticated:
        try:
            from notifications.models import Notification
            count = Notification.objects.filter(user=request.user, is_read=False).count()
        except Exception:
            count = 0
    else:
        count = 0
    return {'unread_notifications_count': count}

