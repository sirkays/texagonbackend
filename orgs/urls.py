from django.urls import path
from .views import (dashboard_summary,courses_list,courses_stats_header,course_form_options,
    course_create,course_detail,course_update,course_delete,
     ClassroomViewSet,StudentsViewSet,TeacherViewSet, ParentViewSet,SubjectViewSet
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
]

# append, don't overwrite
urlpatterns += router.urls
