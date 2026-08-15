from django.urls import path
from .views import (
    create_live_session,
    user_live_sessions,
    update_live_session_status,
    delete_live_session,
    update_private_tutoring,
    public_session_info,
    toggle_room_access,
    live_session_config,
)

urlpatterns = [
    ##### STUDENT AND TEACHER ENDPOINT ###############
    path("api/get-live-session/", user_live_sessions, name="get-live-session"),
    path("api/config/", live_session_config, name="live_session_config"),
    path("api/live-config/", live_session_config, name="live_session_config_alias"),

    ########## TEACHER ENDPOINT ##########
    path("api/create-live-session/", create_live_session, name="create-live-session"),

    path("api/update-live-session/<str:session_id>/status/", update_live_session_status, name="update_live_session_status"),
    
    path("api/update-live-session/<str:session_id>/", update_live_session_status, name="update_live_session_status"),

    path("api/toggle-room-access/<str:session_id>/", toggle_room_access, name="toggle_room_access"),

    path("api/delete-live-session/<str:session_id>/delete/", delete_live_session, name="delete_live_session"),

    path("api/update-private-tutoring/<str:session_id>/", update_private_tutoring, name="update_private_tutoring"),

    path('public-session/<str:meeting_id>/', public_session_info, name='public-session-info'),
]
