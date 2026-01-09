from django.urls import path
from .views import (achievements_overview,generate_subs,teacher_analytics_view,student_certificates_list,certificate_create,
courses_completion_dashboard,course_completed_enrollments, 

    course_completed_students,
    certificate_admin_approve,
    certificate_teacher_approve,
    certificate_approval_status,
    student_course_activity_metrics,
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

]
