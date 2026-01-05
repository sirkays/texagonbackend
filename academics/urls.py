from django.urls import path
from .views import (achievements_overview,generate_subs,teacher_analytics_view,student_certificates_list,certificate_create)

urlpatterns = [
    path("api/gamification/achievements/", achievements_overview, name="achievements-overview"),
    path("generate_subs/", generate_subs, name="generate_sub"),
    path('teacher-analytics/', teacher_analytics_view, name='teacher_analytics'),

    path('api/certificate/list/', student_certificates_list, name='student_certificates_list'),

    path("api/certificate/create/", certificate_create, name="certificate_create"),
]
