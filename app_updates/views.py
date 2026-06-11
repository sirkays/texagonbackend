from rest_framework.response import Response
from rest_framework.views import APIView

from .models import AppVersion
from .serializers import AppVersionSerializer


def _parse_version(version_str):
    """Parse a version string like '1.2.3' into a tuple of ints for comparison."""
    try:
        return tuple(int(x) for x in version_str.strip().split('.'))
    except (ValueError, AttributeError):
        return (0,)


class CheckUpdateView(APIView):
    """
    Public endpoint (no auth required) to check for app updates.

    Query params:
        platform        — one of: windows, android, ios, macos
        current_version — the client's current version string, e.g. '1.0.0'
    """

    authentication_classes = []
    permission_classes = []

    def get(self, request):
        platform = request.query_params.get('platform')
        current_version = request.query_params.get('current_version')

        if not platform or not current_version:
            return Response(
                {'error': 'Both "platform" and "current_version" query parameters are required.'},
                status=400,
            )

        latest = (
            AppVersion.objects
            .filter(platform=platform, is_active=True)
            .order_by('-created_at')
            .first()
        )

        if latest is None:
            return Response({'update_available': False})

        current = _parse_version(current_version)
        latest_parsed = _parse_version(latest.version)

        update_available = latest_parsed > current

        # Determine if this update should be forced
        force_update = False
        if update_available and latest.is_force_update:
            force_update = True
        if latest.min_supported_version:
            min_supported = _parse_version(latest.min_supported_version)
            if current < min_supported:
                force_update = True
                update_available = True  # ensure flag is set when below min

        serializer = AppVersionSerializer(latest)

        return Response({
            'update_available': update_available,
            'force_update': force_update,
            **serializer.data,
        })
