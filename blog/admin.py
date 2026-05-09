from django.contrib import admin
from .models import BlogPost, Category, Tag, AuthorProfile, NewsletterSubscriber


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(AuthorProfile)
class AuthorProfileAdmin(admin.ModelAdmin):
    search_fields = ['user__email', 'user__first_name', 'user__last_name']
    list_display = ("user", "title")
    raw_id_fields = ("user",)


@admin.register(BlogPost)
class BlogPostAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "category",
        "author",
        "is_featured",
        "is_published",
        "published_at",
    )
    list_filter = ("is_published", "is_featured", "category")
    search_fields = ("title", "excerpt", "content")
    prepopulated_fields = {"slug": ("title",)}
    filter_horizontal = ("tags",)
    raw_id_fields = ("author",)
    date_hierarchy = "published_at"
    readonly_fields = ("created_at", "updated_at")

    fieldsets = (
        (None, {"fields": ("title", "slug", "author", "category", "tags")}),
        (
            "Content",
            {"fields": ("featured_image", "excerpt", "content", "read_time")},
        ),
        (
            "Publishing",
            {
                "fields": (
                    "is_published",
                    "is_featured",
                    "published_at",
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )


@admin.register(NewsletterSubscriber)
class NewsletterSubscriberAdmin(admin.ModelAdmin):
    list_display = ("email", "subscribed_at", "is_active")
    list_filter = ("is_active",)
    search_fields = ("email",)
    readonly_fields = ("subscribed_at",)