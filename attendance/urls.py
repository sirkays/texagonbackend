# attendance/urls.py
from django.urls import path
from . import views

urlpatterns = [
    path("teacher/mark/",                   views.mark_attendance,      name="attendance-mark"),
    path("teacher/<int:course_id>/records/", views.course_attendance,    name="attendance-records"),
    path("teacher/<int:course_id>/auto/",    views.auto_mark_attendance, name="attendance-auto"),
    # Add to attendance/urls.py
    path("teacher/session/<int:session_id>/delete/", views.delete_attendance_session, name="attendance-session-delete"),
]