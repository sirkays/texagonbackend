from django.urls import path
from . import views

app_name = "blog"

urlpatterns = [
    path("list/", views.blog_list, name="blog_list"),
    path("newsletter/subscribe/", views.newsletter_subscribe, name="newsletter_subscribe"),
    # Detail must come last to avoid swallowing other routes
    path("detail/<slug:slug>/", views.blog_detail, name="blog_detail"),
    path("seed-blog/", views.seed_blog_view, name="seed_blog"),
]