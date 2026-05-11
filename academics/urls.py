from django.urls import path
from .views import (achievements_overview,generate_subs,teacher_analytics_view,student_certificates_list,certificate_create,
courses_completion_dashboard,course_completed_enrollments, 

    course_completed_students,
    certificate_admin_approve,
    certificate_teacher_approve,
    certificate_approval_status,
    student_course_activity_metrics,
)

from .report_views import (
    teacher_report_list, teacher_report_create, teacher_report_detail,
    teacher_report_update, teacher_report_publish, teacher_report_delete,
    teacher_report_student_data,
    my_reports_list, my_report_detail,
    public_report_info, public_report_verify_student, public_parent_setup,
)

urlpatterns = [
    path("api/gamification/achievements/", achievements_overview, name="achievements-overview"),
    path("generate_subs/", generate_subs, name="generate_sub"),
    path('teacher-analytics/', teacher_analytics_view, name='teacher_analytics'),

    path('api/certificate/list/', student_certificates_list, name='student_certificates_list'),

    path("api/certificate/create/", certificate_create, name="certificate_create"),

    path("api/courses/completion-dashboard/", courses_completion_dashboard),

    path("api/courses/<int:course_id>/completed-enrollments/", course_completed_enrollments),


    # --- Courses / Enrollments ---
    path(
        "api/courses/<int:course_id>/completed-students/",
        course_completed_students,
        name="course-completed-students",
    ),

    # --- Certificates approvals ---
    path(
        "api/certificates/<int:cert_id>/approval-status/",
        certificate_approval_status,
        name="certificate-approval-status",
    ),
    path(
        "api/certificates/<int:cert_id>/approve/teacher/",
        certificate_teacher_approve,
        name="certificate-teacher-approve",
    ),
    path(
        "api/certificates/<int:cert_id>/approve/admin/",
        certificate_admin_approve,
        name="certificate-admin-approve",
    ),

    path(
        "api/courses/<int:course_id>/students/<int:student_id>/activity-metrics/",
        student_course_activity_metrics,
        name="student-course-activity-metrics",
    ),

    # ─── Teacher Reports ────────────────────────────────────
    path("api/reports/", teacher_report_list, name="teacher-report-list"),
    path("api/reports/create/", teacher_report_create, name="teacher-report-create"),
    path("api/reports/<int:report_id>/", teacher_report_detail, name="teacher-report-detail"),
    path("api/reports/<int:report_id>/update/", teacher_report_update, name="teacher-report-update"),
    path("api/reports/<int:report_id>/publish/", teacher_report_publish, name="teacher-report-publish"),
    path("api/reports/<int:report_id>/delete/", teacher_report_delete, name="teacher-report-delete"),
    path("api/reports/student-data/", teacher_report_student_data, name="teacher-report-student-data"),

    # ─── Student/Parent Report Viewing ──────────────────────
    path("api/my-reports/", my_reports_list, name="my-reports-list"),
    path("api/my-reports/<int:report_id>/", my_report_detail, name="my-report-detail"),

    # ─── Public Report Access ───────────────────────────────
    path("api/report/public/<str:token>/", public_report_info, name="public-report-info"),
    path("api/report/public/<str:token>/verify/", public_report_verify_student, name="public-report-verify"),
    path("api/report/public/<str:token>/parent-setup/", public_parent_setup, name="public-parent-setup"),

]
