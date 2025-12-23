# app: api/ide/urls.py
from django.urls import path
from . import views

urlpatterns = [
    # snippets
    path("api/ide/snippets/", views.snippet_list, name="ide_snippet_list"),          # GET (student)
    path("api/ide/snippets/create/", views.snippet_create, name="ide_snippet_create"),  # POST (student)
    path("snippets/<int:snippet_id>/delete/", views.snippet_delete, name="snippet_delete"),
    path("api/ide/snippets/<int:snippet_id>/", views.snippet_detail, name="ide_snippet_detail"),  # GET (student)

    # submissions
    path("api/ide/submissions/create/", views.submission_create, name="ide_submission_create"),   # POST (student)
    path("api/ide/student/submissions/", views.student_submission_list, name="ide_submission_list"),  
    path("api/ide/submissions/<int:submission_id>/", views.submission_detail, name="ide_submission_detail"),  # GET (student/teacher)
    path("api/ide/submissions/<int:submission_id>/teacher-update/", views.submission_teacher_update, name="ide_submission_teacher_update"),  # PATCH (teacher)

    # comments
    path("api/ide/submissions/<int:submission_id>/comments/", views.submission_comment_create, name="ide_submission_comment_create"),  # POST (student/teacher)
    
    path("api/ide/files/", views.codefile_list, name="ide_codefile_list"),
    path("api/ide/files/upload/", views.codefile_upload, name="ide_codefile_upload"),
    path("api/ide/files/<int:file_id>/", views.codefile_detail, name="ide_codefile_detail"),
    path("api/ide/files/<int:file_id>/delete/", views.codefile_delete, name="ide_codefile_delete"),
    
    path("api/teacher/submissions/", views.teacher_submissions_list, name="teacher_submissions_list"),
    path("api/teacher/submissions/<int:pk>/", views.teacher_submission_detail, name="teacher_submission_detail"),
    path("api/teacher/submissions/<int:pk>/comments/", views.teacher_submission_comments, name="teacher_submission_comments"),
    path("api/teacher/submissions/<int:pk>/grade/", views.teacher_submission_grade, name="teacher_submission_grade"),
]
