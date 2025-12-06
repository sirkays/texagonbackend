from django.urls import path
from .import views
urlpatterns = [
    path("api/tests/available/", views.available_tests, name="available-tests"),
    path("api/tests/<int:test_id>/submit/", views.submit_test, name="submit-test"),


    path('api/teacher/tests/', views.teacher_tests_list, name='teacher_tests_list'),
    path('api/teacher/tests/create/', views.create_test, name='create_test'),
    path('api/teacher/tests/<int:test_id>/', views.teacher_test_detail, name='teacher_test_detail'),
    path('api/teacher/tests/<int:test_id>/update/', views.update_test, name='update_test'),
    path('api/teacher/tests/<int:test_id>/delete/', views.delete_test, name='delete_test'),
    path('api/teacher/tests/<int:test_id>/publish/', views.publish_test, name='publish_test'),
    path('api/teacher/tests/<int:test_id>/duplicate/', views.duplicate_test, name='duplicate_test'),
    
    # Question management
    path('api/teacher/tests/<int:test_id>/questions/add/', views.add_question, name='add_question'),
    path('api/teacher/tests/<int:test_id>/questions/<int:question_id>/update/', views.update_question, name='update_question'),
    path('api/teacher/tests/<int:test_id>/questions/<int:question_id>/delete/', views.delete_question, name='delete_question'),
    
    # Helper endpoints
    path('api/teacher/courses/', views.teacher_courses, name='teacher_courses'),


    ##### TEST ATTEMPTS #################
    path('api/student/test-attempts/', views.my_test_attempts, name='my_test_attempts'),

    path('api/student/cbt-quit/', views.cbt_test_quit, name='cbt_test_quit'),


    path('api/student/performance-summary/', views.student_performance_summary, name='student_performance_summary'),

    path('api/student-list/performance/', views.student_performance_list, name='student_performance_list'),

    path('api/student-detail/performance/', views.student_performance_detail, name='student_performance_detail'),

    path('api/teacher/module-analytics/', views.teacher_module_analytics, name='teacher_module_analytics'),


    path('api/teacher/fetch-my-test/', views.fetch_my_tests, name='fetch_my_tests'),
]

