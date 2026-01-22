# core/permissions.py
from rest_framework.permissions import BasePermission

class IsAdminAccess(BasePermission):
    """
    Allow only staff/superuser OR users with adminaccess relation.
    """
    message = "Admin access required."

    def has_permission(self, request, view):
        u = getattr(request, "user", None)
        if not u or not u.is_authenticated:
            return False
        if getattr(u, "is_staff", False) or getattr(u, "is_superuser", False):
            return True
        return bool(getattr(u, "adminaccess", None))
