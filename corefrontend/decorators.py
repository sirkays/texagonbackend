from functools import wraps

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.contrib import messages
from django.shortcuts import redirect


def dashboard_access_required(view_func):
    @login_required(login_url='corefrontend:dashboard_login')
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        access = getattr(request.user, 'applications_dashboard_access', None)

        if not access or not access.is_active or not access.can_view_dashboard:
            messages.error(request, "You do not have access to the applications dashboard.")
            return redirect('corefrontend:dashboard_login')

        return view_func(request, *args, **kwargs)

    return _wrapped_view


def dashboard_export_required(view_func):
    @login_required(login_url='corefrontend:dashboard_login')
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        user = request.user

        access = getattr(user, 'applications_dashboard_access', None)

        if not access or not access.is_active or not access.can_view_dashboard or not access.can_export_csv:
            return HttpResponseForbidden("You do not have permission to export applications.")

        return view_func(request, *args, **kwargs)

    return _wrapped_view