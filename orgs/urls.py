from django.urls import path
from .views import dashboard_summary,ClassroomViewSet
from rest_framework.routers import DefaultRouter

urlpatterns = [
    path("api/admin/dashboard/summary/", dashboard_summary, name="dashboard-summary"),
]


router = DefaultRouter()
router.register(r"api/classrooms", ClassroomViewSet, basename="classroom")

urlpatterns = router.urls