from django.urls import path
from .views import my_materials
urlpatterns = [
    path("api/materials/mine/", my_materials, name="my-materials"),
]