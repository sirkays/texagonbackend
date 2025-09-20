from django.urls import path
from .import views
urlpatterns = [
    path("api/post-login/", views.post_login, name="post_login"),
    path("api/dashboard/overview/", views.dashboard_overview, name="dashboard-overview"),

    # PARENT OVERVIEW
    path("api/dashboard/parent/overview/", views.parent_overview, name="parent-overview"),
]