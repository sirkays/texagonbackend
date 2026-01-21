from django.urls import path
from . import views

urlpatterns = [
    path("api/my-notifications/", views.my_notifications, name="my-notifications"),
    path("api/my-notifications/<int:notification_id>/read/", views.update_notification_read_state, name="notification-read"),
    path("api/my-notifications/read-bulk/", views.bulk_update_notifications_read_state, name="notifications-read-bulk"),
]
