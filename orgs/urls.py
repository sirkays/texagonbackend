from django.urls import path
from .views import dashboard_summary, ClassroomViewSet
from rest_framework.routers import DefaultRouter

router = DefaultRouter()
router.register(r"api/classrooms", ClassroomViewSet, basename="classroom")

urlpatterns = [
    path("api/admin/dashboard/summary/", dashboard_summary, name="dashboard-summary"),
]

# append, don't overwrite
urlpatterns += router.urls
