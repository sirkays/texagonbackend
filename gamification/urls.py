from django.urls import path
from .views import leaderboard_overview,parent_rewards

urlpatterns = [
    path("api/leaderboard/", leaderboard_overview, name="leaderboard-overview"),

    path("api/child/rewards/", parent_rewards, name="parent_rewards"),
]
