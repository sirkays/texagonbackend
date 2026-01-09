from django.urls import path
from .views import (dashboard_summary,courses_list,courses_stats_header,course_form_options,
    course_create,course_detail,course_update,course_delete,
     ClassroomViewSet,StudentsViewSet,TeacherViewSet, ParentViewSet,SubjectViewSet,
     modules_list, module_lessons,billing_dashboard, invoice_detail,
         gamification_summary,
    badges_view, badge_detail,
    achievements_view, achievement_detail,
    gamification_leaderboard,
    admin_student_enrollments,admin_available_courses_for_student

)
from rest_framework.routers import DefaultRouter

router = DefaultRouter()
router.register(r"api/classrooms", ClassroomViewSet, basename="classroom")
router.register(r"api/admin/students", StudentsViewSet, basename="admin-students")
router.register(r"api/admin/teachers", TeacherViewSet, basename="teacher")
router.register(r"api/parents", ParentViewSet, basename="parents")
router.register(r"api/subjects", SubjectViewSet, basename="subject")

urlpatterns = [
    path("api/admin/dashboard/summary/", dashboard_summary, name="dashboard-summary"),

    path("api/admin/courses/",courses_list, name="courses-list"),
    path("api/admin/courses/stats/", courses_stats_header, name="courses-stats"),
    path("api/admin/courses/options/", course_form_options, name="courses-options"),
    path("api/admin/courses/create/", course_create, name="course-create"),
    path("api/admin/courses/<int:course_id>/", course_detail, name="course-detail"),
    path("api/admin/courses/<int:course_id>/update/", course_update, name="course-update"),
    path("api/admin/courses/<int:course_id>/delete/", course_delete, name="course-delete"),

    path("api/admin/module/list/", modules_list, name="module-admin-list"),
    path("api/admin/module/lessons/<int:module_id>/", module_lessons, name="module-admin-lesson"),

    path("api/admin/billing/dashboard", billing_dashboard, name="billing_dashboard"),
    path("api/admin/billing/invoices/<int:invoice_id>", invoice_detail, name="invoice_detail"),

    path("api/admin/gamification/summary", gamification_summary),
    path("api/admin/gamification/badges", badges_view),
    path("api/admin/gamification/badges/<int:badge_id>", badge_detail),
    path("api/admin/gamification/achievements", achievements_view),
    path("api/admin/gamification/achievements/<int:achievement_id>", achievement_detail),
    path("api/admin/gamification/leaderboard", gamification_leaderboard),

    path("api/admin/students/<int:student_id>/enrollments/", admin_student_enrollments),
    path("api/admin/students/<int:student_id>/available-courses/", admin_available_courses_for_student),
]
# append, don't overwrite
urlpatterns += router.urls
