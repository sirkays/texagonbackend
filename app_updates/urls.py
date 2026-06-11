from django.urls import path

from .views import CheckUpdateView

urlpatterns = [
    path('api/app-updates/check/', CheckUpdateView.as_view(), name='check-update'),
]
