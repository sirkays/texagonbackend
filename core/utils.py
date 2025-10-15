from orgs.models import OrganizationMembership,Organization
from academics.models import StudentProfile,TeacherProfile
from gamification.models import Badge, BadgeAward, PointTransaction, Streak  
from django.db.models import Q, Sum, Count
from django.utils import timezone
from typing import Any, Dict, List, Optional,Literal, Tuple
import traceback
from decimal import Decimal
from django.shortcuts import _get_queryset
from accounts.models import User

StatusLiteral = Literal["active", "inactive", "suspended"]



def _avatar_url_for(user: User, request) -> str | None:
    if not getattr(user, "avatar", None):
        return None
    try:
        return request.build_absolute_uri(user.avatar.url)
    except Exception:
        return getattr(user.avatar, "url", None)


def _get_or_create_parent_membership(user: User, org: Organization) -> OrganizationMembership:
    membership, _ = OrganizationMembership.objects.get_or_create(
        user=user,
        organization=org,
        role=OrganizationMembership.Role.PARENT,
        defaults={"is_active": True},
    )
    return membership


def _is_admin(request) -> bool:
    # Treat staff/superuser or AdminAccess as "admin" for elevated operations like avatar updates
    if getattr(request.user, "is_staff", False) or getattr(request.user, "is_superuser", False):
        return True
    return bool(getattr(request.user, "adminaccess", None))




# ---------------- helpers ----------------
def _get_student_for_user(user) -> Optional[StudentProfile]:
    mem = (OrganizationMembership.objects
           .filter(user=user, is_active=True)
           .select_related("organization")
           .order_by("-id")
           .first())
    if mem:
        sp = StudentProfile.objects.filter(user=user, organization=mem.organization).first()
        if sp:
            return sp
    return StudentProfile.objects.filter(user=user).order_by("-id").first()


def _get_teacher_for_user(user) -> Optional[TeacherProfile]:
    mem = (OrganizationMembership.objects
           .filter(user=user, is_active=True)
           .select_related("organization")
           .order_by("-id")
           .first())
    if mem:
        sp = TeacherProfile.objects.filter(user=user, organization=mem.organization).first()
        if sp:
            return sp
    return TeacherProfile.objects.filter(user=user).order_by("-id").first()

def _to_int(v) -> int:
    try:
        return int(v or 0)
    except Exception:
        return 0


def _sum_points(student: StudentProfile) -> int:
    return int(PointTransaction.objects.filter(student=student).aggregate(t=Sum("points")).get("t") or 0)




def get_object_or_404_ajax(klass, *args, custom_message=None, **kwargs):
    queryset = _get_queryset(klass)

    try:
        obj = queryset.get(*args, **kwargs)
    except queryset.model.DoesNotExist:
        # Log the error
        #logger.error(f'Object not found: {klass} with args={args}, kwargs={kwargs}')

        # Raise Http404 with custom message if provided
        if custom_message:
            return custom_message
        else:
            return False
    
    return obj



def _status_from_user_membership(user: User, membership: OrganizationMembership | None) -> StatusLiteral:
    """
    Map DB flags to the UI status chip:
      - suspended  -> user.is_active == False
      - inactive   -> user.is_active == True AND (no membership OR membership.is_active == False)
      - active     -> user.is_active == True AND membership.is_active == True
    """
    if not user.is_active:
        return "suspended"
    if not membership or not membership.is_active:
        return "inactive"
    return "active"




def _apply_status_to_user_membership(status_value: StatusLiteral,
                                     user: User,
                                     membership: OrganizationMembership) -> None:
    """
    Apply the UI status back onto DB flags.
    """
    if status_value == "suspended":
        user.is_active = False
        membership.is_active = False
    elif status_value == "inactive":
        user.is_active = True
        membership.is_active = False
    else:  # "active"
        user.is_active = True
        membership.is_active = True


# If Course creation activity is desired:
# from learning.models import Course

def _resolve_org(request):

    admin_access = getattr(request.user, "adminaccess", None)
    if admin_access is None or admin_access.selected_organization is None:
        org_id = request.query_params.get("org_id") or getattr(getattr(request.user, "primary_org", None), "id", None)
        if not org_id:
            return None, Response({"detail": "Organization not specified and no primary_org on user."},
                                status=status.HTTP_400_BAD_REQUEST)
        try:
            org = Organization.objects.get(id=org_id, is_active=True)
        except Organization.DoesNotExist:
            return None, Response({"detail": "Organization not found."}, status=status.HTTP_404_NOT_FOUND)
            
        # Ensure caller is a member
        is_member = OrganizationMembership.objects.filter(
            user=request.user, organization=org, is_active=True
        ).exists()
        if not is_member:
            return None, Response({"detail": "You do not have access to this organization."},
                                status=status.HTTP_403_FORBIDDEN)
    else:
        org = admin_access.selected_organization
    
    return org, None
