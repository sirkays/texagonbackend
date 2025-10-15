from orgs.models import OrganizationMembership,Organization
from academics.models import StudentProfile,TeacherProfile,Classroom, Subject
from gamification.models import Badge, BadgeAward, PointTransaction, Streak  
from django.db.models import Q, Sum, Count
from django.utils import timezone
from typing import Any, Dict, List, Optional,Literal, Tuple
import traceback
from decimal import Decimal
from django.shortcuts import _get_queryset
from accounts.models import User
from learning.models import Course, Module, Enrollment

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





# ---------- helpers ----------

def _is_org_admin_or_teacher(request, org) -> bool:
    """
    Admins (staff/superuser) or members of org with role ADMIN/OWNER/TEACHER.
    """
    u = request.user
    if getattr(u, "is_staff", False) or getattr(u, "is_superuser", False):
        return True
    return OrganizationMembership.objects.filter(
        user=u,
        organization=org,
        role__in=[
            OrganizationMembership.Role.OWNER,
            OrganizationMembership.Role.ADMIN,
            OrganizationMembership.Role.TEACHER,
        ],
        is_active=True,
    ).exists()


def _course_to_card_dict(c) -> dict:
    """Shape a Course queryset row (with annotations) to the UI card object."""
    teacher_name = (
        getattr(getattr(c, "teacher", None), "user", None).get_full_name()
        or getattr(getattr(c, "teacher", None), "user", None).email
        or ""
    )
    subject_name = getattr(c.subject, "name", "")
    classroom_name = getattr(c.classroom, "name", "")

    avg_progress = c.avg_progress or Decimal("0")
    # Normalize progress to plain int percentage for UI
    try:
        progress_int = int(round(Decimal(avg_progress)))
    except Exception:
        progress_int = 0

    return {
        "id": c.id,
        "name": c.name,
        "subject": subject_name,
        "teacher": teacher_name,
        "classroom": classroom_name,
        "students": c.students_count or 0,
        "modules": c.modules_count or 0,
        "status": "active" if c.is_active else "inactive",
        "progress": progress_int,
    }


def _get_ids_from_payload(data, org):
    """
    Accepts either IDs or names for subject/classroom/teacher; returns validated model IDs.
    Frontend can send {subject_id, classroom_id, teacher_id} (preferred) OR
    {subject, classroom, teacher} as display names.
    """
    # Prefer explicit *_id if present
    subject_id = data.get("subject_id")
    classroom_id = data.get("classroom_id")
    teacher_id = data.get("teacher_id")

    if not subject_id:
        subject_name = data.get("subject")
        if subject_name:
            subj = Subject.objects.filter(organization=org, name=subject_name).first()
            subject_id = getattr(subj, "id", None)

    if not classroom_id:
        classroom_name = data.get("classroom")
        if classroom_name:
            room = Classroom.objects.filter(organization=org, name=classroom_name).first()
            classroom_id = getattr(room, "id", None)

    if not teacher_id:
        teacher_name = data.get("teacher")
        if teacher_name:
            # Try "Full Name" first, fallback to user.email equals
            qs = TeacherProfile.objects.filter(organization=org).select_related("user")
            teacher = qs.filter(
                Q(user__first_name__isnull=False, user__last_name__isnull=False) &
                Q(user__first_name__icontains=teacher_name.split(" ")[0]) &
                Q(user__last_name__icontains=teacher_name.split(" ")[-1])
            ).first() or qs.filter(user__email__iexact=teacher_name).first()
            teacher_id = getattr(teacher, "id", None)

    return subject_id, classroom_id, teacher_id
