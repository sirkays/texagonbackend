from django.urls import path
from .views import my_materials,learning_modules,save_lesson_to_my_materials
urlpatterns = [
    path("api/materials/mine/", my_materials, name="my-materials"),
    path("api/modules/learning/", learning_modules, name="learning-modules"),
    path("api/save/lesson/<int:lesson_id>/", save_lesson_to_my_materials, name="save-lesson"),
]