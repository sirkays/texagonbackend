from django.urls import path
from .views import create_live_session,student_live_sessions,update_live_session_status

urlpatterns = [
    path("api/create-live-session/", create_live_session, name="create-live-session"),
    path("api/get-live-session/", student_live_sessions, name="get-live-session"),
    path("api/update-live-session/<str:session_id>/status/", update_live_session_status, name="update_live_session_status"),
]
