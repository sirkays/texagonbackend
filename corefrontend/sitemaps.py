from django.contrib.sitemaps import Sitemap
from django.urls import reverse
from datetime import datetime

# IMPORT YOUR MODELS HERE
from blog.models import BlogPost 
from projects.models import StudentProject 


class StaticViewSitemap(Sitemap):
    """
    Sitemap for all static pages across your apps.
    Priorities are tuned per page importance for SEO.
    """
    changefreq = 'weekly'

    # Priority map: higher priority pages are more likely to be shown in search results
    _priority_map = {
        'corefrontend:home_view': 1.0,
        'corefrontend:programs': 0.9,
        'corefrontend:for_schools': 0.9,
        'corefrontend:techxablocks': 0.8,
        'corefrontend:techxaforge': 0.8,
        'corefrontend:projects': 0.8,
        'corefrontend:contact': 0.7,
        'corefrontend:apply_partner': 0.7,
        'corefrontend:become_a_tutor': 0.7,
        'corefrontend:team': 0.6,
        'corefrontend:gallery': 0.5,
        'blog:blog_list': 0.8,
        'projects:project_list': 0.7,
        'corefrontend:terms': 0.3,
        'corefrontend:privacy': 0.3,
    }

    def items(self):
        # We only include public-facing, indexable pages.
        return [
            # Core Frontend Pages
            'corefrontend:home_view',
            'corefrontend:programs',
            'corefrontend:for_schools',
            'corefrontend:projects',
            'corefrontend:contact',
            'corefrontend:apply_partner',
            'corefrontend:team',
            'corefrontend:gallery',
            'corefrontend:techxablocks',
            'corefrontend:techxaforge',
            'corefrontend:become_a_tutor',
            'corefrontend:terms',
            'corefrontend:privacy',
            
            # List Pages from other apps
            'projects:project_list',
            'blog:blog_list',
        ]

    def priority(self, item):
        return self._priority_map.get(item, 0.5)

    def location(self, item):
        return reverse(item)


class BlogSitemap(Sitemap):
    """
    Dynamic Sitemap for Blog Posts.
    """
    changefreq = "weekly"
    priority = 0.9

    def items(self):
        return BlogPost.objects.filter(is_published=True) 

    def lastmod(self, obj):
        return obj.updated_at 

    def location(self, obj):
        return reverse('blog:blog_detail', kwargs={'slug': obj.slug})


class ProjectSitemap(Sitemap):
    """
    Dynamic Sitemap for Student Projects.
    """
    changefreq = "monthly"
    priority = 0.7

    def items(self):
        return StudentProject.objects.filter(is_published=True)

    def lastmod(self, obj):
        return obj.updated_at

    def location(self, obj):
        return reverse('projects:project_detail', kwargs={'slug': obj.slug})