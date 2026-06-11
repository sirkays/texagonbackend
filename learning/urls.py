from django.urls import path
from . import views
urlpatterns = [
    ##### STUDENT ENDPOINTS ##########
    path("api/materials/mine/", views.my_materials, name="my-materials"),
    path('api/materials/delete/', views.delete_saved_material, name='delete_saved_material'),

    path("api/modules/learning/", views.learning_modules, name="learning-modules"),
    path("api/save/lesson/<int:lesson_id>/", views.save_lesson_to_my_materials, name="save-lesson"),
    path("api/academics/resources/", views.resource_materials, name="resource-materials"),

    path("api/student/lesson/", views.my_lessons, name="my-lessons"),

    path("api/student/courses/", views.my_courses, name="my-courses"),


    ##### TEACHER ENDPOINTS ##########
    path('api/teacher/modules/', views.list_modules, name='teacher_list_modules'),
    path('api/teacher/modules/<int:module_id>/', views.get_module, name='teacher_get_module'),
    path('api/teacher/modules/create/', views.create_module, name='teacher_create_module'),
    path('api/teacher/modules/<int:module_id>/update/', views.update_module, name='teacher_update_module'),
    path('api/teacher/modules/<int:module_id>/delete/', views.delete_module, name='teacher_delete_module'),
    path('api/teacher/modules/<int:module_id>/publish/', views.publish_module, name='teacher_publish_module'),

    path("api/teacher/courses/<int:course_id>/general-activation/", views.update_course_general_activation),
    
    # Lesson management
    path('api/teacher/modules/<int:module_id>/lessons/', views.add_lesson, name='teacher_add_lesson'),
    path('api/teacher/modules/<int:module_id>/lessons/<int:lesson_id>/', views.update_lesson, name='teacher_update_lesson'),
    path('api/teacher/modules/<int:module_id>/lessons/<int:lesson_id>/delete/', views.delete_lesson, name='teacher_delete_lesson'),
    
    # Helper endpoints
    path('api/teacher/courses/', views.get_teacher_courses, name='teacher_get_courses'),
    path('api/teacher/module-categories/', views.get_module_categories, name='teacher_get_module_categories'),


    path('api/cloudinary-signature/', views.cloudinary_signature, name='cloudinary_signature'),

    path('api/presign-s3/', views.presign_s3, name='create_presigned_upload'),

    path('api/lesson-media-url/<int:lesson_id>/', views.lesson_media_url, name='lesson_media_url'),
    
    path('api/stream-video/<int:lesson_id>/', views.stream_local_video, name='stream_local_video'),

    path('api/toggle-code-submit/', views.toggle_code_submit, name='toggle_code_submit'),
    
    
]