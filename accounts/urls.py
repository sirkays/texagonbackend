from django.urls import path
from .import views
urlpatterns = [
    path("api/post-login/", views.post_login, name="post_login"),
]