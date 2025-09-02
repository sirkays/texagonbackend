from django.urls import path
from .views import leaderboard_overview

urlpatterns = [
    path("api/leaderboard/", leaderboard_overview, name="leaderboard-overview"),
]
