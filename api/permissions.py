# permissions.py
from rest_framework.permissions import BasePermission, SAFE_METHODS
from django.utils import timezone
from django.db.models import Q

from billing.models import UserAccountSubscription
from academics.models import StudentProfile
from api.services.session_tokens import revoke_all_user_sessions

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




def RequiresActiveStudentSubscription(*, allow_page=False):

    class _RequiresActiveStudentSubscription(BasePermission):
        """
        Enforce: student subscription must be ACTIVE and not expired.

        - If the logged-in user is a student -> checks their own subscription.
        - If the logged-in user is a parent -> requires a student_id context.
        - Automatically revokes ALL session tokens if subscription is invalid.
        """

        message = "Subscription expired or inactive. Please subscribe again."

        def _get_student_id(self, request, view):
            return (
                getattr(view, "kwargs", {}).get("student_id")
                or getattr(view, "kwargs", {}).get("id")
                or (request.data.get("student_id") if hasattr(request, "data") else None)
                or request.query_params.get("student_id")
            )

        def _is_subscription_active(self, *, student_profile, org_id, user_id) -> bool:
            now = timezone.now()

            return UserAccountSubscription.has_subscription(student_profile.user, student_profile.organization)

        def has_permission(self, request, view):
            user = request.user
            if not user or not user.is_authenticated:
                return False

            # =========================
            # STUDENT CONTEXT
            # =========================
            if request.session.get('allowed_courses_cache') and request.session.get('allowed_courses_cache').get('date_cached') is None:
                del request.session['allowed_courses_cache']
                
            student_profile = getattr(user, "student_profile", None)
            if allow_page:
                data,returned_count = student_profile.get_course_allowed(request, is_session=True)
 
                if returned_count == 2:
                    return len(data) == 2
                return data
            
            is_allowed = student_profile.check_session_data(request)
            ##### IF allow_page=True is not passed then RequiresActiveStudentSubscription()
            if is_allowed:
                return True


            if student_profile:
                active = self._is_subscription_active(
                    student_profile=student_profile,
                    org_id=student_profile.organization_id,
                    user_id=user.id,
                )

                if not active:
                    # 🔥 REVOKE ALL TOKENS IMMEDIATELY
                    revoke_all_user_sessions(user, reason="student_subscription_expired")
                    return False

                return True

            # =========================
            # PARENT CONTEXT
            # =========================
            parent_profile = getattr(user, "parent_profile", None)
            if parent_profile:
                student_id = self._get_student_id(request, view)
                if not student_id:
                    return False

                student = (
                    StudentProfile.objects
                    .only("id", "user_id", "organization_id")
                    .filter(
                        id=student_id,
                        organization_id=parent_profile.organization_id,
                        parent_links__parent=parent_profile,
                    )
                    .first()
                )
                if not student:
                    return False
                active = self._is_subscription_active(
                    student_profile=student,
                    org_id=student.organization_id,
                    user_id=student.user_id,
                )

                if not active:
                    # 🔥 Revoke ONLY the student’s sessions, not the parent’s
                    revoke_all_user_sessions(
                        student.user,
                        reason="student_subscription_expired",
                    )
                    return False

                return True

            # =========================
            # OTHER ROLES (admin, staff)
            # =========================
            return True

    return _RequiresActiveStudentSubscription