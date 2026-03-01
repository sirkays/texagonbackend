# urls.py
from django.urls import path
from . import views

urlpatterns = [

    path("start-room/", views.start_room, name="start_room"),

    path("join-room/", views.join_room, name="join_room"),

    path("get-allowed-room/", views.get_room_allowed, name="get_room_allowed"),
]
