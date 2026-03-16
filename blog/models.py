from django.db import models
from accounts.models import User
from django.utils.text import slugify
from django.utils import timezone


class Category(models.Model):
    name = models.CharField(max_length=100)
    slug = models.SlugField(unique=True, max_length=120)

    class Meta:
        verbose_name_plural = "categories"
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)


class Tag(models.Model):
    name = models.CharField(max_length=50)
    slug = models.SlugField(unique=True, max_length=70)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)


class AuthorProfile(models.Model):
    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name="author_profile"
    )
    bio = models.TextField(blank=True)
    avatar = models.ImageField(upload_to="avatars/", blank=True, null=True)
    title = models.CharField(max_length=100, blank=True)

    def __str__(self):
        return self.user.get_full_name() or self.user.username

    @property
    def display_name(self):
        return self.user.get_full_name() or self.user.username


class BlogPost(models.Model):
    title = models.CharField(max_length=200)
    slug = models.SlugField(unique=True, max_length=250)
    featured_image = models.ImageField(upload_to="blog/", blank=True, null=True)
    excerpt = models.TextField(max_length=300, help_text="Short summary shown in listings")
    content = models.TextField()
    category = models.ForeignKey(
        Category, on_delete=models.SET_NULL, null=True, related_name="posts"
    )
    tags = models.ManyToManyField(Tag, blank=True, related_name="posts")
    author = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, related_name="blog_posts"
    )
    read_time = models.PositiveIntegerField(
        default=5, help_text="Estimated read time in minutes"
    )
    is_featured = models.BooleanField(
        default=False, help_text="Show as the featured post at the top of the blog"
    )
    is_published = models.BooleanField(default=False)
    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-published_at"]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title)
        # Auto-set published_at when first published
        if self.is_published and not self.published_at:
            self.published_at = timezone.now()
        super().save(*args, **kwargs)


class NewsletterSubscriber(models.Model):
    email = models.EmailField(unique=True)
    subscribed_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.email