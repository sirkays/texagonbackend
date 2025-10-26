# permissions.py
from rest_framework.permissions import BasePermission, SAFE_METHODS

class IsStudent(BasePermission):
    """
    Allows access only to users that have a StudentProfile.
    """

    message = "Only students can access this endpoint."

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated and hasattr(user, "student_profile")):
            return False
        # e.g., require their org and/or classroom to exist, or custom flag
        return True
