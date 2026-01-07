# urls.py
from django.urls import path
from .views import (active_modules_for_user,leaderboard_seasons_view,leaderboard_season_detail_view,leaderboard_season_set_active_view)

urlpatterns = [
    path("api/academics/modules/active/", active_modules_for_user, name="active-modules-for-user"),
    path("api/admin/settings/leaderboard-seasons/", leaderboard_seasons_view),
    path("api/admin/settings/leaderboard-seasons/<int:season_id>/", leaderboard_season_detail_view),
    path("api/admin/settings/leaderboard-seasons/<int:season_id>/set-active/", leaderboard_season_set_active_view),
]
