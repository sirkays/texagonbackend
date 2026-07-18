from django.urls import path
from . import views

urlpatterns = [
    # OPW CRUD
    path("api/works/", views.opw_list_create, name="opw_list_create"),
    path("api/works/<int:opw_id>/", views.opw_detail, name="opw_detail"),

    # Students with current scores
    path("api/works/<int:opw_id>/students/", views.opw_students, name="opw_students"),

    # Upsert scores
    path("api/works/<int:opw_id>/scores/submit/", views.opw_submit_scores, name="opw_submit_scores"),

    # CSV/Excel export and import
    path("api/works/<int:opw_id>/scores/export/", views.opw_export_csv, name="opw_export_csv"),
    path("api/works/<int:opw_id>/scores/export-excel/", views.opw_export_excel, name="opw_export_excel"),
    path("api/works/<int:opw_id>/scores/import-excel/", views.opw_import_excel, name="opw_import_excel"),

    # Helper lookups
    path("api/courses/", views.opw_teacher_courses, name="opw_teacher_courses"),
    path("api/classrooms/", views.opw_course_classrooms, name="opw_course_classrooms"),
]
