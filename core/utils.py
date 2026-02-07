from orgs.models import OrganizationMembership,Organization
from academics.models import StudentProfile,TeacherProfile,Classroom, Subject,EnrollmentCertificate
from gamification.models import Badge, BadgeAward, PointTransaction, Streak,AchievementDefinition,LeaderboardSeason
from django.db.models import Q, Sum, Count
from django.utils import timezone
from typing import Any, Dict, List, Optional,Literal, Tuple
import traceback
from decimal import Decimal
from django.shortcuts import _get_queryset
from accounts.models import User
from learning.models import Course, Module, Enrollment,Lesson,Module,CoursePassCriteria
from calendar import monthrange
from rest_framework.exceptions import PermissionDenied, ValidationError
from datetime import date, datetime, timedelta
from rest_framework import permissions
import hmac, hashlib, uuid
from django.conf import settings
import secrets
from django.utils.dateparse import parse_datetime

StatusLiteral = Literal["active", "inactive", "suspended"]

COOKIE_NAME = "device_id"

def get_or_make_device_id(request):
    # Prefer explicit header from native clients; fall back to cookie for browsers.
    dev = request.META.get("HTTP_X_DEVICE_ID") or request.COOKIES.get(COOKIE_NAME)
    return dev or uuid.uuid4().hex  # generate if missing

def user_agent(request):
    return request.META.get("HTTP_USER_AGENT", "")

def client_ip(request):
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")

def hash_ip(ip: str) -> str:
    if not ip:
        return ""
    return hmac.new(settings.SECRET_KEY.encode(), ip.encode(), hashlib.sha256).hexdigest()

# ---------- helpers specific to this view ----------

def _member_display_name(membership: OrganizationMembership) -> str:
    if not membership:
        return ""
    u = membership.user
    full = (getattr(u, "get_full_name", lambda: "")() or "").strip()
    return full or u.email or f"user-{u.pk}"


def _month_bounds(dt=None):
    now = dt or timezone.now()
    y, m = now.year, now.month
    first = timezone.make_aware(datetime(y, m, 1, 0, 0, 0))
    last_day = monthrange(y, m)[1]
    last = timezone.make_aware(datetime(y, m, last_day, 23, 59, 59))
    return first, last

# ---------- helpers ----------

def _module_to_card_dict(m: Module) -> dict:
    """
    Shape a Module row to the UI card object used in your Modules grid.
    """
    return {
        "id": m.id,
        "name": m.name,
        "course": getattr(m.course, "name", ""),
        "order": m.order,
        "difficulty": m.difficulty,  # "BEGINNER" | "INTERMEDIATE" | "ADVANCED"
        "lessons": m.lessons_count or 0,
        "duration": int(m.duration_minutes or 0),  # minutes
        "category": getattr(m.category, "name", "") if m.category_id else "",
        "active": bool(m.active),
    }


def _lesson_to_modal_row(l: Lesson, idx: int) -> dict:
    """
    Shape a Lesson row for the Lessons modal.
    (Your modal currently hardcodes 'completed', so we default to False.)
    """
    # Convert seconds to whole minutes, safe for None
    duration_min = int((l.duration_seconds or 0) // 60)
    return {
        "id": l.id,
        "title": l.name,
        "duration": duration_min,
        "type": l.content_type,  # "video" | "audio" | "pdf" | "doc" | "link"
        "completed": False,      # you can wire real progress later if available
        "order": l.order,
    }


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
        "description":c.description,
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





# ----------------------------------------
# Helpers
# ----------------------------------------

def _int(v, default=0):
    try:
        return int(v)
    except Exception:
        return default

def _json_or_dict(value) -> dict:
    if isinstance(value, dict):
        return value
    try:
        return json.loads(value or "{}")
    except Exception:
        return {}

def _badge_to_dict(b: Badge) -> dict:
    return {
        "id": b.id,
        "name": b.name,
        "icon_name": b.icon_name,
        "color": b.color,
        "points": b.points,
        "criteria": b.criteria or "",
        "rules": b.rules or {},
    }

def _ach_to_dict(a: AchievementDefinition) -> dict:
    return {
        "id": a.id,
        "code": a.code,
        "title": a.title,
        "description": a.description or "",
        "icon": a.icon,
        "category": a.category,
        "points": a.points,
        "is_active": bool(a.is_active),
    }



def _lesson_belongs_to_org(lesson: Lesson, org) -> bool:
    """
    Verify lesson -> module -> course -> organization == org
    """
    if not lesson or not org:
        return False
    # Avoid extra queries when possible by walking FKs
    try:
        return lesson.module.course.organization_id == org.id
    except Exception:
        # Fallback if any relation is missing
        l = Lesson.objects.select_related("module__course__organization").filter(id=lesson.id).first()
        return bool(l and l.module and l.module.course and l.module.course.organization_id == org.id)


class IsOwnerOrOrgStaff(permissions.BasePermission):
    """
    - Students: must own the object (Bookmark/Note.student points to their StudentProfile)
    - Org admins/teachers: allowed for all objects (still org-scoped by queryset)
    """

    def has_object_permission(self, request, view, obj):
        org, _ = _resolve_org(request)
        if _is_org_admin_or_teacher(request, org):
            return True
        sp = _get_student_for_user(request.user)
        return bool(sp and getattr(obj, "student_id", None) == sp.id)




def _gen_cert_number(prefix="CERT"):
    # short, unique-ish human ID. If you already have a generator, use it instead.
    return f"{prefix}-{timezone.now():%Y%m%d}-{secrets.token_hex(4).upper()}"






def _cert_to_dict(cert: EnrollmentCertificate, request=None) -> dict:
    # best-effort URLs
    pdf_url = ""
    if getattr(cert, "pdf_file", None):
        try:
            pdf_url = request.build_absolute_uri(cert.pdf_file.url) if request else cert.pdf_file.url
        except Exception:
            pdf_url = getattr(cert.pdf_file, "url", "") or ""

    student_name = ""
    try:
        u = cert.student.user
        student_name = (u.get_full_name() or u.email or "").strip()
    except Exception:
        pass

    course_name = ""
    try:
        course_name = cert.course.name
    except Exception:
        pass

    return {
        "id": cert.id,
        "number": cert.number,
        "status": cert.status,
        "title": cert.title,
        "description": cert.description or "",
        "student_id": cert.student_id,
        "student_name": student_name,
        "enrollment_id": cert.enrollment_id,
        "course_id": cert.course_id,
        "course_name": course_name,
        "acquired_at": cert.acquired_at,
        "downloadable_at": cert.downloadable_at,
        "can_download": cert.can_download,
        "pdf_url": pdf_url if cert.can_download else "",  # gate the URL
    }



def _org_signatures_to_dict(org, request=None) -> dict:
    sig = getattr(org, "certificate_signatures", None)
    if not sig:
        return {
            "director_1": {"name": "", "title": "", "signature_url": ""},
            "director_2": {"name": "", "title": "", "signature_url": ""},
        }

    def abs_url(file_field):
        if not file_field:
            return ""
        try:
            return request.build_absolute_uri(file_field.url) if request else file_field.url
        except Exception:
            return getattr(file_field, "url", "") or ""

    return {
        "director_1": {
            "name": sig.director_1_name or "",
            "title": sig.director_1_title or "",
            "signature_url": abs_url(sig.director_1_signature),
        },
        "director_2": {
            "name": sig.director_2_name or "",
            "title": sig.director_2_title or "",
            "signature_url": abs_url(sig.director_2_signature),
        },
    }


def resolve_season(org, occurred_at):
    
    # 1) Prefer active season if it contains the date
    active = LeaderboardSeason.get_active(None)
    if active and active.contains(occurred_at):
        return active

    # 2) Otherwise find by date range
    return (LeaderboardSeason.objects
            .filter(start_at__lte=occurred_at, end_at__gt=occurred_at)
            .order_by("-start_at")
            .first())



def _parse_dt(value):
    """
    Accept ISO 8601 string. Example:
      "2026-01-01T00:00:00Z" or "2026-01-01T00:00:00+01:00"
    """
    if not value:
        return None
    if isinstance(value, str):
        dt = parse_datetime(value)
        if dt is None:
            return None
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, timezone.get_current_timezone())
        return dt
    return None

def _season_to_dict(s: LeaderboardSeason):
    return {
        "id": s.id,
        "organization_id": s.organization_id,
        "name": s.name,
        "slug": s.slug,
        "start_at": s.start_at.isoformat() if s.start_at else None,
        "end_at": s.end_at.isoformat() if s.end_at else None,
        "is_active": s.is_active,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }

def _criteria_to_dict(c: CoursePassCriteria):
    return {
        "course_id": c.course_id,
        "no_of_cbt": c.no_of_cbt,
        "no_of_code_submission": c.no_of_code_submission,
        "total_pass_mark_cbt": c.total_pass_mark_cbt,
        "total_pass_mark_code": c.total_pass_mark_code,
    }


def _parse_positive_int(val, field_name, default=None):
    """
    Accepts int-like strings; returns (value, error_response_or_None)
    """
    if val is None:
        return default, None
    try:
        n = int(val)
    except (TypeError, ValueError):
        return None, Response(
            {"detail": f"{field_name} must be an integer."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if n < 0:
        return None, Response(
            {"detail": f"{field_name} must be >= 0."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return n, None


def _is_course_teacher(request, cert: EnrollmentCertificate) -> bool:
    # depends on your Course model shape; adjust accordingly
    # you used c.teacher as TeacherProfile in _course_to_card_dict
    try:
        return cert.course.teacher.user_id == request.user.id
    except Exception:
        return False


def _enrollment_to_dict(e: Enrollment) -> dict:
    c = e.course
    teacher_name = ""
    try:
        teacher_name = c.teacher.user.get_full_name() or c.teacher.user.email or ""
    except Exception:
        pass

    return {
        "id": e.id,
        "status": e.status,
        "progress_pct": float(e.progress_pct or Decimal("0")),
        "completed_at": e.completed_at,
        "created_at": getattr(e, "created_at", None),

        "course": {
            "id": c.id,
            "name": c.name,
            "subject": getattr(getattr(c, "subject", None), "name", ""),
            "classroom": getattr(getattr(c, "classroom", None), "name", ""),
            "teacher": teacher_name,
            "is_active": bool(getattr(c, "is_active", True)),
            "course_type": getattr(c, "course_type", "public"),
        },
    }






def _get_user_avatar_url(request, user) -> str | None:
    """
    Optional: if you store avatar/photo on User model.
    Adjust this to your actual user field (e.g. user.avatar, user.photo, etc).
    """
    avatar = getattr(user, "avatar", None) or getattr(user, "photo", None)
    if not avatar:
        return None
    try:
        return request.build_absolute_uri(avatar.url)
    except Exception:
        return None


def _try_fetch_courses_for_classroom(classroom: Classroom):
    """
    Courses are not directly linked to Classroom in the snippet you shared.
    If you have learning.Enrollment with (student -> StudentProfile, course -> Course),
    we can infer courses from students in this classroom.
    If your schema differs, edit here.
    """
    try:
        from learning.models import Enrollment  # type: ignore
    except Exception:
        return [], 0

    qs = (
        Enrollment.objects.select_related("course")
        .filter(student__current_classroom_id=classroom.id)
        .values("course_id", "course__name")
        .distinct()
        .order_by("course__name")
    )

    courses = [{"id": row["course_id"], "title": row["course__name"]} for row in qs]
    return courses, len(courses)



def _get_admin_selected_org_id(request):
    adminaccess = getattr(request.user, "adminaccess", None)
    if not adminaccess or not adminaccess.active:
        return None
    if not adminaccess.selected_organization_id:
        return None
    return adminaccess.selected_organization_id



def _clean_excluded_students(data, course):
    """
    Returns a list of StudentProfile IDs that are enrolled in the given course.
    Raises Response on invalid.
    """
    raw = data.get("excluded_students", None)
    if raw is None:
        return None  # means "do not touch excluded_users"

    if not isinstance(raw, list):
        raise ValueError("excluded_students must be a list of StudentProfile IDs.")

    # keep only ints
    try:
        ids = [int(x) for x in raw]
    except (TypeError, ValueError):
        raise ValueError("excluded_students must be a list of integers.")

    ids = list({i for i in ids if i > 0})  # unique, positive

    if not ids:
        return []  # means "clear all exclusions"

    # validate these profiles are enrolled (active) for this course
    enrolled_user_ids = Enrollment.objects.filter(
        course=course,
        completed_at__isnull=True,
    ).values_list("student_id", flat=True)

    allowed_profile_ids = set(
        StudentProfile.objects.filter(user_id__in=enrolled_user_ids)
        .values_list("id", flat=True)
    )

    bad = [i for i in ids if i not in allowed_profile_ids]
    if bad:
        raise ValueError(f"Some students are not enrolled in this course: {bad}")

    return ids
