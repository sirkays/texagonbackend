from django.contrib import admin

from .models import AppVersion


@admin.register(AppVersion)
class AppVersionAdmin(admin.ModelAdmin):
    list_display = ('version', 'platform', 'build_number', 'is_force_update', 'is_active', 'created_at')
    list_filter = ('platform', 'is_force_update', 'is_active')
    search_fields = ('version', 'release_notes')
    list_editable = ('is_active', 'is_force_update')
    readonly_fields = ('created_at',)
    ordering = ('-created_at',)
