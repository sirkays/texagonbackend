from django.urls import path
from .views import achievements_overview

urlpatterns = [
    path("api/gamification/achievements/", achievements_overview, name="achievements-overview"),
]
