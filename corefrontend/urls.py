from django.urls import path
from .import views

app_name = 'corefrontend'

urlpatterns = [
    path('terms/', views.terms,name="terms"),
    path('privacy/', views.privacy,name="privacy"),

    path("create-superuser/", views.create_superuser_view, name="create_superuser"),

    path('', views.home_view,name="home_view"),

    path('programs/', views.programs,name="programs"),

    path('for-schools/', views.for_schools,name="for_schools"),

    path('projects/', views.projects,name="projects"),

    path('project/robotics/', views.project_detail,name="project_detail"),

    path('contact/', views.contact,name="contact"),

    path('apply-partner/', views.apply_partner,name="apply_partner"),

    path('team/', views.team,name="team"),
    path('gallery/', views.gallery,name="gallery"),

    path("techxablocks/", views.techxablocks, name="techxablocks"),
    path("techxaforge/", views.techxaforge, name="techxaforge"),

    path('apply-tutor/',        views.become_a_tutor,         name='become_a_tutor'),
    path('application-form/',      views.application_form,        name='application_form'),
    path('save-application-step/', views.save_application_step,   name='save_application_step'),
    path('submit-application/',    views.submit_application,       name='submit_application'),


    path('admin-user/applications/',          views.admin_applications,         name='admin_applications'),
    path('admin-user/applications/<int:pk>/', views.admin_application_detail,   name='admin_application_detail'),
    path('admin-user/send-email/',            views.admin_send_email,           name='admin_send_email'),
]