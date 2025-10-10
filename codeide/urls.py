# app: api/ide/urls.py
from django.urls import path
from . import views

urlpatterns = [
    # snippets
    path("api/ide/snippets/", views.snippet_list, name="ide_snippet_list"),          # GET (student)
    path("api/ide/snippets/create/", views.snippet_create, name="ide_snippet_create"),  # POST (student)
    path("api/ide/snippets/<int:snippet_id>/", views.snippet_detail, name="ide_snippet_detail"),  # GET (student)

    # submissions
    path("api/ide/submissions/create/", views.submission_create, name="ide_submission_create"),   # POST (student)
    path("api/ide/submissions/<int:submission_id>/", views.submission_detail, name="ide_submission_detail"),  # GET (student/teacher)
    path("api/ide/submissions/<int:submission_id>/teacher-update/", views.submission_teacher_update, name="ide_submission_teacher_update"),  # PATCH (teacher)

    # comments
    path("api/ide/submissions/<int:submission_id>/comments/", views.submission_comment_create, name="ide_submission_comment_create"),  # POST (student/teacher)
]
