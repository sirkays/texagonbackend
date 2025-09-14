from django.urls import path
from .views import achievements_overview,generate_subs

urlpatterns = [
    path("api/gamification/achievements/", achievements_overview, name="achievements-overview"),
    path("generate_subs/", generate_subs, name="generate_sub"),
]
