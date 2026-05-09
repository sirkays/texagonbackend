from django.urls import path
from .import views
urlpatterns = [
    path("test_email/", views.test_email, name="test_email"),
    path("create_admin/", views.create_admin, name="create_admin"),
    path("api/post-login/", views.post_login, name="post_login"),
    path("api/dashboard/overview/", views.dashboard_overview, name="dashboard-overview"),

    path("api/reset-password/", views.ResetPasswordView.as_view({"post": "post"}), name="api-reset-password"),
    path("api/confirm-email-change/", views.ConfirmEmailChangeView.as_view({"post": "post"}), name="api-confirm-email"),
    path("api/update-profile/", views.update_profile, name="update_profile"),
    path("api/fetch-profile/", views.fetch_profile, name="fetch_profile"),


    # PARENT OVERVIEW
    path("api/dashboard/parent/overview/", views.parent_overview, name="parent-overview"),
    path('api/parent/children-progress/', views.children_progress_view, name='children_progress'),

    path('api/parent/children-list/', views.children_list_view, name='children_list'),
    path('api/parent/time-periods/', views.time_periods_view, name='time_periods'),

    path('api/parent/children/', views.get_children_progress, name='parent-children'),
    path('api/parent/reset-child-password/', views.reset_child_password, name='reset-child-password'),


    ##### ENDPOINT FOR SETTING ADMIN ACCESS
    path('api/set-admin/access-orgs/', views.set_admin_access_orgs, name='set_admin_access_orgs'),
    path('api/fetch-admin/access-orgs/', views.fetch_admin_access_orgs, name='fetch_admin_access_orgs'),

    path('api/teacher/overview/', views.teacher_dashboard_overview, name='teacher_dashboard_overview'),

    path("api/account/create/", views.create_account_view, name="create-account"),
    path("api/auth/verify-email/", views.verify_email_view, name="verify-email"),
    path("api/auth/verify-email-auth/", views.verify_email_view_authenticated),
    path("api/auth/resend-email-otp/", views.resend_email_otp_view, name="resend-email-otp"),
    path("api/parent/resume/", views.resume_parent_flow_view),

    path("api/auth/fetch-user/", views.fetch_user_detail, name="fetch-user-detail"),
    path("api/auth/verify-user/", views.verify_and_update_user, name="verify-user"),

    path("api/update-parent-child-link/", views.update_parent_child_link, name="update_parent_child_link"),

    path("api/auth/verify-password/", views.verify_password, name="verify-password"),

]