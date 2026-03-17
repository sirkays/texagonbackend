from django.contrib.sitemaps import Sitemap
from django.urls import reverse

# IMPORT YOUR MODELS HERE
# Make sure these match the actual names of your models in your apps
from blog.models import BlogPost 
from projects.models import StudentProject 

class StaticViewSitemap(Sitemap):
    """
    Sitemap for all static pages across your apps.
    """
    priority = 0.8
    changefreq = 'weekly'

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

    def location(self, item):
        return reverse(item)


class BlogSitemap(Sitemap):
    """
    Dynamic Sitemap for Blog Posts.
    """
    changefreq = "weekly"
    priority = 0.9

    def items(self):
        # Adjust this query if you have a 'status' or 'is_published' field
        return BlogPost.objects.filter(is_published=True) 

    def lastmod(self, obj):
        # Assumes your Post model has an 'updated_at' or 'created_at' field
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

    def location(self, obj):
        return reverse('projects:project_detail', kwargs={'slug': obj.slug})