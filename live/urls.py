from django.urls import path
from .views import create_live_session,student_live_sessions

urlpatterns = [
    path("api/create-live-session/", create_live_session, name="create-live-session"),
    path("api/get-live-session/", student_live_sessions, name="get-live-session"),
]
