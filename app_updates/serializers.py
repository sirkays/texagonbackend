from rest_framework import serializers

from .models import AppVersion


class AppVersionSerializer(serializers.ModelSerializer):
    class Meta:
        model = AppVersion
        fields = [
            'version', 'build_number', 'platform', 'download_url',
            'file_size', 'release_notes', 'is_force_update',
            'min_supported_version', 'created_at',
        ]
