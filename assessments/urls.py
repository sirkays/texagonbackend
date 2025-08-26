from django.urls import path
from .import views
urlpatterns = [
    path("api/tests/available/", views.available_tests, name="available-tests"),
    path("api/tests/<int:test_id>/submit/", views.submit_test, name="submit-test"),
]