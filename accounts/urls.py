from django.urls import path
from .import views
urlpatterns = [
    path("api/post-login/", views.post_login, name="post_login"),
    path("api/dashboard/overview/", views.dashboard_overview, name="dashboard-overview"),

    # PARENT OVERVIEW
    path("api/dashboard/parent/overview/", views.parent_overview, name="parent-overview"),
    path('api/parent/children-progress/', views.children_progress_view, name='children_progress'),

    path('api/parent/children-list/', views.children_list_view, name='children_list'),
    path('api/parent/time-periods/', views.time_periods_view, name='time_periods'),
]