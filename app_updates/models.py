from django.db import models

from core.models import TimeStampedModel


class AppVersion(TimeStampedModel):
    """Tracks application versions and updates for each platform."""

    PLATFORM_CHOICES = [
        ('windows', 'Windows'),
        ('android', 'Android'),
        ('ios', 'iOS'),
        ('macos', 'macOS'),
    ]

    version = models.CharField(max_length=20)
    build_number = models.IntegerField()
    platform = models.CharField(max_length=20, choices=PLATFORM_CHOICES)
    download_url = models.URLField(max_length=500)
    file_size = models.BigIntegerField(null=True, blank=True)
    release_notes = models.TextField(blank=True)
    is_force_update = models.BooleanField(default=False)
    min_supported_version = models.CharField(max_length=20, blank=True, default='')
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['-created_at']
        unique_together = ['version', 'platform']

    def __str__(self):
        return f'{self.platform} v{self.version} (build {self.build_number})'
