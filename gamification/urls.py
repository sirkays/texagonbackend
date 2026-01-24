from django.urls import path
from .views import (leaderboard_overview,parent_rewards,
    admin_gamification_meta,
    admin_achievement_definitions,
    admin_achievement_definition_update,
    admin_achievement_definition_deactivate,
    admin_badges,
    admin_badge_update,
    admin_badge_deactivate,
)

urlpatterns = [
    path("api/leaderboard/", leaderboard_overview, name="leaderboard-overview"),

    path("api/child/rewards/", parent_rewards, name="parent_rewards"),

    ## gamification admin
    path("api/admin/gamification/meta", admin_gamification_meta),

    path("api/admin/gamification/achievements", admin_achievement_definitions),
    path("api/admin/gamification/achievements/<int:pk>", admin_achievement_definition_update),
    path("api/admin/gamification/achievements/<int:pk>/deactivate", admin_achievement_definition_deactivate),

    path("api/admin/gamification/badges", admin_badges),
    path("api/admin/gamification/badges/<int:pk>", admin_badge_update),
    path("api/admin/gamification/badges/<int:pk>/deactivate", admin_badge_deactivate),

]
