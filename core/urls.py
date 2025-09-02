# urls.py
from django.urls import path
from .views import active_modules_for_user

urlpatterns = [
    path("api/academics/modules/active/", active_modules_for_user, name="active-modules-for-user"),
]
