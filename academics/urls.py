from django.urls import path
from .views import (achievements_overview,generate_subs,teacher_analytics_view,student_certificates_list,certificate_create,
courses_completion_dashboard,course_completed_enrollments, 

    course_completed_students,
    certificate_admin_approve,
    certificate_teacher_approve,
    certificate_approval_status,
    student_course_activity_metrics,
    upload_manual_certificates,
    list_manual_certificates,
    certificate_layout_config,
    # Certificate Request Portal
    public_cert_request_orgs,
    public_cert_request_courses,
    public_cert_request_submit,
    public_cert_request_status,
    admin_list_cert_requests,
    admin_approve_cert_request,
    admin_reject_cert_request,
)

from .report_views import (
    teacher_report_list, teacher_report_create, teacher_report_detail,
    teacher_report_update, teacher_report_publish, teacher_report_delete,
    teacher_report_student_data,
    my_reports_list, my_report_detail,
    public_report_info, public_report_verify_student, public_parent_setup,
    parent_report_by_token,
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

    path("api/certificates/manual-upload/", upload_manual_certificates, name="upload_manual_certificates"),
    path("api/certificates/manual-list/", list_manual_certificates, name="list_manual_certificates"),
    path("api/certificates/layout/", certificate_layout_config, name="certificate_layout_config"),

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

    # ─── Parent: Report by Share Token ──────────────────────
    path("api/report/parent/<str:token>/", parent_report_by_token, name="parent-report-by-token"),

    # ─── Public Certificate Request Portal (no auth) ─────────
    path("api/certificates/public/orgs/", public_cert_request_orgs, name="public-cert-orgs"),
    path("api/certificates/public/courses/", public_cert_request_courses, name="public-cert-courses"),
    path("api/certificates/public/request/", public_cert_request_submit, name="public-cert-request"),
    path("api/certificates/public/status/<str:access_id>/", public_cert_request_status, name="public-cert-status"),

    # ─── Admin Certificate Request Management (auth required) ─
    path("api/certificates/requests/", admin_list_cert_requests, name="admin-cert-requests"),
    path("api/certificates/requests/<int:request_id>/approve/", admin_approve_cert_request, name="admin-cert-approve"),
    path("api/certificates/requests/<int:request_id>/reject/", admin_reject_cert_request, name="admin-cert-reject"),

]
