from django.urls import path
from . import views

app_name = "projects"

urlpatterns = [
    path("students/",              views.project_list,   name="project_list"),
    path("detail/<slug:slug>/",  views.project_detail, name="project_detail"),
    path("seed-projects/", views.seed_projects_view, name="seed_projects"),
]