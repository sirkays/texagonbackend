
from django.contrib import admin
from django.urls import path, include
from django.conf.urls.static import static
from django.contrib.auth import views as auth_views
from django.conf import settings
from django.http import JsonResponse
from django.views.generic import TemplateView
from django.contrib.sitemaps.views import sitemap
from corefrontend.sitemaps import StaticViewSitemap # Import your sitemap classes
# from blog.sitemaps import BlogSitemap

# Create a dictionary of your sitemaps
sitemaps = {
    'static': StaticViewSitemap,
    # 'blog': BlogSitemap,
}

def healthz(_request):
    return JsonResponse({"ok": True})

urlpatterns = [
    path('admin/', admin.site.urls),
    path("healthz/", healthz),
    path('api/', include('api.urls')),
    path('accounts/', include('accounts.urls')),
    path('assessments/', include('assessments.urls')),
    path('learning/', include('learning.urls')),
    path('academics/', include('academics.urls')),
    path('gamification/', include('gamification.urls')),
    path('core/', include('core.urls')),
    path('live/', include('live.urls')),
    path('billing/', include('billing.urls')),
    path('orgs/', include('orgs.urls')),
    path('code-ide/', include('codeide.urls')),
    path('store/api/', include('store.urls')),
    path('notifications/', include('notifications.urls')),
    path('api/attendance/', include('attendance.urls')),
    path('konnect/', include('konnect.urls')),

    path('', include('app_updates.urls')),
    path('', include('corefrontend.urls')),
    path('blog/', include('blog.urls')),
    path('projects/', include('projects.urls')),
    path('robots.txt', TemplateView.as_view(template_name="robots.txt", content_type="text/plain")),
    path('sitemap.xml', sitemap, {'sitemaps': sitemaps}, name='django.contrib.sitemaps.views.sitemap'),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)