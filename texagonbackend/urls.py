
from django.contrib import admin
from django.urls import path, include
from django.conf.urls.static import static
from django.contrib.auth import views as auth_views
from django.conf import settings
urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include('api.urls')),
    path('accounts/', include('accounts.urls')),
    path('assessments/', include('assessments.urls')),
    path('learning/', include('learning.urls')),
    path('academics/', include('academics.urls')),
    path('gamification/', include('gamification.urls')),
    path('core/', include('core.urls')),
    path('live/', include('live.urls')),
    path('billing/', include('billing.urls')),
]
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
