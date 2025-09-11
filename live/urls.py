from django.urls import path
from .views import create_live_session

urlpatterns = [
    path("api/create-live-session/", create_live_session, name="create-live-session"),
]
