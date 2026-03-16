from django.db import models
from accounts.models import User
from django.utils.text import slugify


# Colour + gradient pairs used in the template when no thumbnail exists
THUMB_GRADIENT_CHOICES = [
    ("red-orange",    "Red → Orange"),
    ("orange-slate",  "Orange → Slate"),
    ("slate-red",     "Slate → Red"),
    ("blue-teal",     "Blue → Teal"),
    ("purple-pink",   "Purple → Pink"),
    ("green-teal",    "Green → Teal"),
]

THUMB_EMOJI_CHOICES = [
    ("🌐", "Web / Globe"),
    ("🤖", "Robotics"),
    ("📊", "Data / Chart"),
    ("🔐", "Cybersecurity"),
    ("🎨", "UI/UX / Design"),
    ("💻", "Coding / Laptop"),
    ("⚙️",  "Engineering / Gear"),
    ("📱", "Mobile App"),
    ("🏗️",  "Hardware / Build"),
    ("🧩", "Blocks / Logic"),
]

DIFFICULTY_CHOICES = [
    ("beginner",      "Beginner"),
    ("intermediate",  "Intermediate"),
    ("advanced",      "Advanced"),
]


class ProjectCategory(models.Model):
    name = models.CharField(max_length=100)
    slug = models.SlugField(unique=True, max_length=120)
    colour = models.CharField(
        max_length=40,
        default="red",
        help_text="Tailwind-compatible label: red, orange, blue, purple, green…",
    )

    class Meta:
        verbose_name_plural = "project categories"
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)


class ProjectTag(models.Model):
    name = models.CharField(max_length=60)
    slug = models.SlugField(unique=True, max_length=80)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)


class StudentProject(models.Model):
    # ── Core identity ──────────────────────────────────────────────
    title       = models.CharField(max_length=200)
    slug        = models.SlugField(unique=True, max_length=250)
    subtitle    = models.CharField(
        max_length=200, blank=True,
        help_text="One-line tagline shown on cards and the hero.",
    )
    description = models.TextField(
        help_text="Full project description (supports HTML)."
    )
    excerpt     = models.CharField(
        max_length=300,
        help_text="Short summary shown on listing cards (~120 chars).",
    )

    # ── Taxonomy ───────────────────────────────────────────────────
    category    = models.ForeignKey(
        ProjectCategory,
        on_delete=models.SET_NULL, null=True, related_name="projects",
    )
    tags        = models.ManyToManyField(ProjectTag, blank=True, related_name="projects")
    difficulty  = models.CharField(
        max_length=20, choices=DIFFICULTY_CHOICES, default="beginner"
    )

    # ── Media ──────────────────────────────────────────────────────
    thumbnail   = models.ImageField(
        upload_to="projects/thumbnails/", blank=True, null=True,
        help_text="Card thumbnail. If blank the emoji + gradient fallback is used.",
    )
    # Extra gallery images
    image_1     = models.ImageField(upload_to="projects/gallery/", blank=True, null=True)
    image_2     = models.ImageField(upload_to="projects/gallery/", blank=True, null=True)
    image_3     = models.ImageField(upload_to="projects/gallery/", blank=True, null=True)

    # Fallback display when no thumbnail
    thumb_emoji    = models.CharField(
        max_length=8, choices=THUMB_EMOJI_CHOICES, default="💻",
        help_text="Emoji shown in card when no thumbnail is uploaded.",
    )
    thumb_gradient = models.CharField(
        max_length=20, choices=THUMB_GRADIENT_CHOICES, default="red-orange",
    )

    # Optional video embed (YouTube/Vimeo full URL)
    video_url   = models.URLField(blank=True)

    # Optional live demo / repo links
    demo_url    = models.URLField(blank=True, verbose_name="Live demo URL")
    repo_url    = models.URLField(blank=True, verbose_name="Source code / repo URL")

    # ── Authorship ─────────────────────────────────────────────────
    student_name   = models.CharField(max_length=200)
    student_school = models.CharField(max_length=200, blank=True)
    student_photo  = models.ImageField(
        upload_to="projects/students/", blank=True, null=True
    )
    # Optional: link to a Django user if the student has an account
    student_user   = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="student_projects",
    )

    # ── Status + meta ──────────────────────────────────────────────
    is_published  = models.BooleanField(default=False)
    is_featured   = models.BooleanField(
        default=False,
        help_text="Pin to the top of the homepage projects strip.",
    )
    completed_at  = models.DateField(
        null=True, blank=True,
        help_text="When the project was finished / showcased.",
    )
    created_at    = models.DateTimeField(auto_now_add=True)
    updated_at    = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_featured", "-completed_at", "-created_at"]

    def __str__(self):
        return f"{self.title} — {self.student_name}"

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(f"{self.title}-{self.student_name}")
            slug = base
            n = 1
            while StudentProject.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f"{base}-{n}"
                n += 1
            self.slug = slug
        super().save(*args, **kwargs)

    # ── Helpers ────────────────────────────────────────────────────
    @property
    def gallery_images(self):
        """Non-null gallery images as a list."""
        return [img for img in [self.image_1, self.image_2, self.image_3] if img]

    GRADIENT_CSS = {
        "red-orange":   "linear-gradient(135deg,rgba(232,41,28,.12),rgba(249,115,22,.12))",
        "orange-slate": "linear-gradient(135deg,rgba(249,115,22,.12),rgba(100,116,139,.1))",
        "slate-red":    "linear-gradient(135deg,rgba(100,116,139,.1),rgba(232,41,28,.1))",
        "blue-teal":    "linear-gradient(135deg,rgba(59,130,246,.12),rgba(20,184,166,.1))",
        "purple-pink":  "linear-gradient(135deg,rgba(168,85,247,.12),rgba(236,72,153,.1))",
        "green-teal":   "linear-gradient(135deg,rgba(34,197,94,.12),rgba(20,184,166,.1))",
    }

    @property
    def thumb_gradient_css(self):
        return self.GRADIENT_CSS.get(self.thumb_gradient, self.GRADIENT_CSS["red-orange"])