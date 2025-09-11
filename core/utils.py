from orgs.models import OrganizationMembership
from academics.models import StudentProfile,TeacherProfile
from gamification.models import Badge, BadgeAward, PointTransaction, Streak  
from django.db.models import Q, Sum, Count
from django.utils import timezone
from typing import Any, Dict, List, Optional
import traceback
from decimal import Decimal

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
    print(mem)
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

