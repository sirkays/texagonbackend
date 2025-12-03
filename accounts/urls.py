from django.urls import path
from .import views
urlpatterns = [
    path("create_admin/", views.create_admin, name="create_admin"),
    path("api/post-login/", views.post_login, name="post_login"),
    path("api/dashboard/overview/", views.dashboard_overview, name="dashboard-overview"),

    # PARENT OVERVIEW
    path("api/dashboard/parent/overview/", views.parent_overview, name="parent-overview"),
    path('api/parent/children-progress/', views.children_progress_view, name='children_progress'),

    path('api/parent/children-list/', views.children_list_view, name='children_list'),
    path('api/parent/time-periods/', views.time_periods_view, name='time_periods'),

    path('api/parent/children/', views.get_parent_children, name='parent-children'),
    path('api/parent/reset-child-password/', views.reset_child_password, name='reset-child-password'),


    ##### ENDPOINT FOR SETING ADMIN ACCESS

    path('api/set-admin/access-orgs/', views.set_admin_access_orgs, name='set_admin_access_orgs'),
    path('api/fetch-admin/access-orgs/', views.fetch_admin_access_orgs, name='fetch_admin_access_orgs'),

    path('api/teacher/overview/', views.teacher_dashboard_overview, name='teacher_dashboard_overview'),

    path("api/account/create/", views.create_account_view, name="create-account"),
    path("api/auth/verify-email/", views.verify_email_view, name="verify-email"),
]