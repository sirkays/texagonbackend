from django.urls import path
from .views import my_materials,learning_modules
urlpatterns = [
    path("api/materials/mine/", my_materials, name="my-materials"),
    path("api/modules/learning/", learning_modules, name="learning-modules"),
]