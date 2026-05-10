# codeide/urls.py
from django.urls import path
from . import views
from . import project_views

urlpatterns = [
    # ---- FOLDERS ----
    path("api/ide/folders/", views.folder_list, name="ide_folder_list"),
    path("api/ide/folders/create/", views.folder_create, name="ide_folder_create"),
    path("api/ide/folders/<int:folder_id>/", views.folder_update, name="ide_folder_update"),
    path("api/ide/folders/<int:folder_id>/delete/", views.folder_delete, name="ide_folder_delete"),

    # ---- SNIPPETS (student) ----
    path("api/ide/snippets/", views.snippet_list, name="ide_snippet_list"),
    path("api/ide/snippets/create/", views.snippet_create, name="ide_snippet_create"),
    path("snippets/<int:snippet_id>/delete/", views.snippet_delete, name="snippet_delete"),
    path("api/ide/snippets/<int:snippet_id>/", views.snippet_detail, name="ide_snippet_detail"),

    # ---- PROJECT SUBMISSIONS (student) ----
    path("api/ide/projects/submit/", project_views.project_submit, name="ide_project_submit"),
    path("api/ide/projects/<int:pk>/resubmit/", project_views.project_resubmit, name="ide_project_resubmit"),
    path("api/ide/student/projects/", project_views.student_project_list, name="ide_student_project_list"),
    path("api/ide/student/projects/<int:pk>/", project_views.student_project_detail, name="ide_student_project_detail"),

    # ---- FILES ----
    path("api/ide/files/", views.codefile_list, name="ide_codefile_list"),
    path("api/ide/files/upload/", views.codefile_upload, name="ide_codefile_upload"),
    path("api/ide/files/<int:file_id>/", views.codefile_detail, name="ide_codefile_detail"),
    path("api/ide/files/<int:file_id>/delete/", views.codefile_delete, name="ide_codefile_delete"),

    # ---- TEACHER ----
    path("api/teacher/submissions/", project_views.teacher_projects_list, name="teacher_projects_list"),
    path("api/teacher/submissions/<int:pk>/", project_views.teacher_project_detail, name="teacher_project_detail"),
    path("api/teacher/submissions/<int:pk>/comments/", project_views.teacher_project_comments, name="teacher_project_comments"),
    path("api/teacher/submissions/<int:pk>/grade/", project_views.teacher_project_grade, name="teacher_project_grade"),
    path("api/teacher/submissions/<int:pk>/download/", project_views.teacher_project_download, name="teacher_project_download"),

    # ---- UPLOAD RESOLVE ----
    path("api/uploads/resolve/", views.resolve_upload_by_label, name="resolve-upload-by-label"),
]
