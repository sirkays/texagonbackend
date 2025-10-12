import csv
from datetime import datetime, timedelta
from decimal import Decimal
from io import StringIO
from typing import Literal, Tuple

from django.db import transaction
from django.db.models import Count, F, IntegerField, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.http import HttpResponse
from django.utils import timezone

from rest_framework import mixins, status, viewsets
from rest_framework.decorators import (
    action,
    api_view,
    authentication_classes,
    permission_classes,
)
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework_api_key.permissions import HasAPIKey

from api.authentication import SessionTokenAuthentication

from academics.models import Classroom, StudentProfile, TeacherProfile
from assessments.models import Submission, TestAttempt
from billing.models import SubscriptionPayment
from learning.models import Course, Enrollment
from orgs.models import Organization, OrganizationMembership
from accounts.models import User

from .serializers import (
    ClassroomListSerializer,
    ClassroomWriteSerializer,
    StudentMiniSerializer,
    StudentReadSerializer,
    StudentWriteSerializer,
)


# ---------- Helpers ----------
StatusLiteral = Literal["active", "inactive", "suspended"]

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


class ClassroomViewSet(viewsets.ModelViewSet):
    """
    CRUD for Classrooms (API Key + Session Token).
    - List/search with counts
    - Create/Update return counts too
    """
    permission_classes = [HasAPIKey]
    authentication_classes = [SessionTokenAuthentication]
    queryset = Classroom.objects.all()  # overridden in get_queryset
    serializer_class = ClassroomListSerializer

    def get_serializer_class(self):
        if self.action in {"create", "update", "partial_update"}:
            return ClassroomWriteSerializer
        return ClassroomListSerializer

    def get_queryset(self):
        org, error = _resolve_org(self.request)
        if error:
            return Classroom.objects.none()

        q = Classroom.objects.filter(organization=org)

        # optional search (?q=)
        q_param = self.request.query_params.get("q")
        if q_param:
            q = q.filter(Q(name__icontains=q_param) | Q(code__icontains=q_param))

        # Robust counts via subqueries to avoid multi-join explosions.
        students_sq = (
            StudentProfile.objects
            .filter(organization=org, current_classroom=OuterRef("pk"))
            .values("current_classroom")
            .annotate(c=Count("id"))
            .values("c")[:1]
        )

        courses_sq = (
            Course.objects
            .filter(organization=org, classroom=OuterRef("pk"))
            .values("classroom")
            .annotate(c=Count("id"))
            .values("c")[:1]
        )

        q = q.annotate(
            students_count=Coalesce(Subquery(students_sq, output_field=IntegerField()), Value(0)),
            teachers_count=Count("teachers", distinct=True),
            courses_count=Coalesce(Subquery(courses_sq, output_field=IntegerField()), Value(0)),
        ).order_by("name")

        return q

    def list(self, request, *args, **kwargs):
        org, error = _resolve_org(request)
        if error:
            return error
        qs = self.get_queryset()
        page = self.paginate_queryset(qs)
        ser = ClassroomListSerializer(page or qs, many=True)
        if page is not None:
            return self.get_paginated_response(ser.data)
        return Response(ser.data)

    def retrieve(self, request, *args, **kwargs):
        org, error = _resolve_org(request)
        if error:
            return error
        instance = self.get_object()
        # instance from queryset is already annotated with *_count
        return Response(ClassroomListSerializer(instance).data)

    def perform_create(self, serializer):
        """Attach organization and enforce uniqueness on (org, name)."""
        org, error = _resolve_org(self.request)
        if error:
            # Bubble up as DRF errors
            from rest_framework.exceptions import PermissionDenied, NotFound, ValidationError
            status_code = error.status_code
            detail = error.data.get("detail", "Access denied.")
            if status_code == status.HTTP_404_NOT_FOUND:
                raise NotFound(detail)
            elif status_code == status.HTTP_403_FORBIDDEN:
                raise PermissionDenied(detail)
            else:
                raise ValidationError(detail)

        name = serializer.validated_data.get("name")
        if Classroom.objects.filter(organization=org, name=name).exists():
            from rest_framework.exceptions import ValidationError
            raise ValidationError({"name": "A classroom with this name already exists in the organization."})

        obj = serializer.save(organization=org)
        self.instance = obj  # used in create()

    # -------- FULL create() with counts attached ----------
    def create(self, request, *args, **kwargs):
        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        self.perform_create(ser)
        instance = self.instance  # created Classroom

        # Attach counts so ClassroomListSerializer (which expects *_count sources) can read them.
        instance.students_count = StudentProfile.objects.filter(
            organization=instance.organization,
            current_classroom=instance
        ).count()
        instance.teachers_count = instance.teachers.count()
        instance.courses_count = Course.objects.filter(
            organization=instance.organization,
            classroom=instance
        ).count()

        out = ClassroomListSerializer(instance).data
        headers = self.get_success_headers(ser.data)
        return Response(out, status=status.HTTP_201_CREATED, headers=headers)

    # -------- FULL partial_update() with counts attached ----------
    def partial_update(self, request, *args, **kwargs):
        org, error = _resolve_org(request)
        if error:
            return error

        instance = self.get_object()
        ser = self.get_serializer(instance, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        instance.refresh_from_db()

        # Recompute counts after update
        instance.students_count = StudentProfile.objects.filter(
            organization=org,
            current_classroom=instance
        ).count()
        instance.teachers_count = instance.teachers.count()
        instance.courses_count = Course.objects.filter(
            organization=org,
            classroom=instance
        ).count()

        out = ClassroomListSerializer(instance).data
        return Response(out)

    # (Optional) You can also implement destroy/export/students actions here if needed.

    # ----- Export CSV -----
    @action(detail=False, methods=["get"], url_path="export")
    def export(self, request, *args, **kwargs):
        org, error = _resolve_org(request)
        if error:
            return error
        qs = self.get_queryset()

        # Build CSV
        buff = StringIO()
        writer = csv.writer(buff)
        writer.writerow(["Name", "Code", "Students", "Teachers", "Courses"])
        for c in qs:
            writer.writerow([c.name, c.code, getattr(c, "students", 0), getattr(c, "teachers", 0), getattr(c, "courses", 0)])

        resp = HttpResponse(buff.getvalue(), content_type="text/csv")
        resp["Content-Disposition"] = f'attachment; filename="classrooms_{org.id}.csv"'
        return resp

    # ----- Manage Students (modal) -----
    @action(detail=True, methods=["get", "post", "delete"], url_path="students")
    def students(self, request, pk=None):
        org, error = _resolve_org(request)
        if error:
            return error
        classroom = self.get_object()

        if request.method == "GET":
            qs = StudentProfile.objects.select_related("user").filter(
                organization=org, current_classroom=classroom
            ).order_by("user__last_name", "user__first_name")
            return Response(StudentMiniSerializer(qs, many=True).data)

        student_id = request.data.get("studentId") or request.query_params.get("studentId")
        if not student_id:
            return Response({"detail": "studentId is required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            student = StudentProfile.objects.get(id=student_id, organization=org)
        except StudentProfile.DoesNotExist:
            return Response({"detail": "Student not found in this organization."}, status=status.HTTP_404_NOT_FOUND)

        if request.method == "POST":
            student.current_classroom = classroom
            student.save(update_fields=["current_classroom"])
            return Response({"detail": "Student added to classroom."})

        # DELETE
        if student.current_classroom_id != classroom.id:
            return Response({"detail": "Student is not in this classroom."}, status=status.HTTP_400_BAD_REQUEST)
        student.current_classroom = None
        student.save(update_fields=["current_classroom"])
        return Response({"detail": "Student removed from classroom."})


@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def dashboard_summary(request):
    """
    Returns dashboard stats and recent activity for the caller's org.
    Auth: API Key + Session Token (same as login_view style).
    """
    org, error = _resolve_org(request)
    if error:
        return error

    now = timezone.now()
    curr_from = now - timedelta(days=30)
    prev_from = now - timedelta(days=60)
    prev_to = curr_from

    # ----- Stats -----
    students_count = StudentProfile.objects.filter(organization=org).count()
    teachers_count = TeacherProfile.objects.filter(organization=org).count()
    active_courses_count = Course.objects.filter(organization=org, is_active=True).count()

    # Revenue = sum of successful payments in last 30 days
    revenue_curr = SubscriptionPayment.objects.filter(
        invoice__subscription__organization=org,
        status=SubscriptionPayment.Status.SUCCESS,
        paid_at__gte=curr_from,
        paid_at__lte=now
    ).aggregate(total=Coalesce(Sum("amount"), Value(Decimal("0"))))["total"]

    revenue_prev = SubscriptionPayment.objects.filter(
        invoice__subscription__organization=org,
        status=SubscriptionPayment.Status.SUCCESS,
        paid_at__gte=prev_from,
        paid_at__lt=prev_to
    ).aggregate(total=Coalesce(Sum("amount"), Value(Decimal("0"))))["total"]

    def pct_change(curr: Decimal, prev: Decimal) -> float:
        prev = Decimal(prev or 0)
        curr = Decimal(curr or 0)
        if prev == 0:
            return 100.0 if curr > 0 else 0.0
        return float(((curr - prev) / prev) * 100)

    data_stats = {
        "students": {"value": students_count, "changePct": None},   # no MoM for counts unless you want to compute it
        "teachers": {"value": teachers_count, "changePct": None},
        "activeCourses": {"value": active_courses_count, "changePct": None},
        "revenue": {
            "value": str(revenue_curr),    # keep as string to avoid float surprises
            "currency": "NGN",
            "changePct": round(pct_change(revenue_curr, revenue_prev), 2),
        },
        "period": {
            "current": {"from": curr_from.isoformat(), "to": now.isoformat()},
            "previous": {"from": prev_from.isoformat(), "to": prev_to.isoformat()},
        },
    }

    # ----- Recent activity (mix of signals) -----
    # Feel free to tailor these to your domain.
    recent = []

    # New student enrollments
    for e in Enrollment.objects.select_related("student__user").filter(
        course__organization=org
    ).order_by("-created_at")[:5]:
        recent.append({
            "id": f"enr-{e.id}",
            "action": "New student enrolled",
            "user": getattr(e.student.user, "get_full_name", lambda: "")() or e.student.user.email,
            "time": e.created_at.isoformat(),
        })

    # Latest submissions
    for s in Submission.objects.select_related("student__user").filter(
        assignment__course__organization=org
    ).order_by("-submitted_at")[:5]:
        recent.append({
            "id": f"sub-{s.id}",
            "action": "Assignment submitted",
            "user": getattr(s.student.user, "get_full_name", lambda: "")() or s.student.user.email,
            "time": s.submitted_at.isoformat(),
        })

    # Test attempts
    for ta in TestAttempt.objects.select_related("student__user").filter(
        test__course__organization=org
    ).order_by("-started_at")[:5]:
        recent.append({
            "id": f"tst-{ta.id}",
            "action": "Test started",
            "user": getattr(ta.student.user, "get_full_name", lambda: "")() or ta.student.user.email,
            "time": ta.started_at.isoformat(),
        })

    # Course creation
    for c in Course.objects.filter(organization=org).order_by("-created_at")[:5]:
        recent.append({
            "id": f"crs-{c.id}",
            "action": "New course created",
            "user": getattr(getattr(c.teacher, "user", None), "email", "") or "—",
            "time": c.created_at.isoformat(),
        })

    # Successful payments
    for p in SubscriptionPayment.objects.select_related("invoice").filter(
        invoice__subscription__organization=org,
        status=SubscriptionPayment.Status.SUCCESS
    ).order_by("-paid_at")[:5]:
        recent.append({
            "id": f"pay-{p.id}",
            "action": "Payment received",
            "user": getattr(getattr(p.invoice, "organization_membership", None), "user", None).email
                    if getattr(p.invoice, "organization_membership", None) else "—",
            "time": p.paid_at.isoformat(),
            "meta": {"amount": str(p.amount), "currency": p.currency},
        })

    # Sort combined feed by newest and trim
    recent.sort(key=lambda x: x["time"], reverse=True)
    recent = recent[:15]

    return Response({
        "stats": data_stats,
        "recentActivity": recent,
    })


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


# ---------- ViewSet ----------
class StudentsViewSet(mixins.ListModelMixin,
                      mixins.CreateModelMixin,
                      mixins.UpdateModelMixin,
                      mixins.DestroyModelMixin,
                      viewsets.GenericViewSet):
    """
    CRUD for students scoped to the caller's organization.
    Auth: API Key + Session Token
    """
    authentication_classes = [SessionTokenAuthentication]
    permission_classes = [HasAPIKey, IsAuthenticated]

    def _org_or_error(self):
        org, error = _resolve_org(self.request)
        return org, error

    def list(self, request, *args, **kwargs):
        org, error = self._org_or_error()
        if error:
            return error

        # Filters to match UI:
        q = (request.query_params.get("q") or "").strip().lower()
        classroom_name = request.query_params.get("classroom")
        status_filter = request.query_params.get("status")  # active | inactive | suspended

        qs = (StudentProfile.objects
              .select_related("user", "current_classroom")
              .filter(organization=org))

        if q:
            qs = qs.filter(
                Q(user__first_name__icontains=q) |
                Q(user__last_name__icontains=q) |
                Q(user__email__icontains=q) |
                Q(admission_no__icontains=q)
            )

        if classroom_name and classroom_name.lower() != "all":
            qs = qs.filter(current_classroom__name=classroom_name)

        # Preload memberships for status mapping
        memberships = {
            m.user_id: m
            for m in OrganizationMembership.objects.filter(
                organization=org, role=OrganizationMembership.Role.STUDENT,
                user_id__in=qs.values_list("user_id", flat=True)
            )
        }

        items = []
        for sp in qs.order_by("user__first_name", "user__last_name"):
            user = sp.user
            membership = memberships.get(user.id)
            status_text = _status_from_user_membership(user, membership)
            items.append({
                "id": sp.id,
                "name": (user.get_full_name() or user.email or str(user.pk)),
                "email": user.email,
                "classroom": getattr(sp.current_classroom, "name", None),
                "admission_no": sp.admission_no,
                "status": status_text,
            })

        if status_filter and status_filter != "all":
            items = [i for i in items if i["status"] == status_filter]

        data = StudentReadSerializer(items, many=True).data
        return Response(data)

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        org, error = self._org_or_error()
        if error:
            return error

        ser = StudentWriteSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        payload = ser.validated_data

        classroom_name = payload.get("classroom") or None
        admission_no = payload.get("admissionNo") or ""
        status_value: StatusLiteral = payload.get("status") or "active"

        # Find/create classroom by name (optional)
        classroom = None
        if classroom_name:
            classroom = Classroom.objects.filter(organization=org, name=classroom_name).first()
            if not classroom:
                return Response({"detail": f"Classroom '{classroom_name}' not found."},
                                status=status.HTTP_400_BAD_REQUEST)

        # Find or create the user by email
        email = payload["email"].lower().strip()
        name = (payload["name"] or "").strip()
        first, last = (name.split(" ", 1) + [""])[:2]

        user, created = User.objects.get_or_create(
            email=email,
            primary_org=org,
            defaults={"first_name": first, "last_name": last or ""}
        )
        if not created:
            # If user exists, update their display name (non-destructive)
            if first and not user.first_name:
                user.first_name = first
            if last and not user.last_name:
                user.last_name = last
            user.save(update_fields=["first_name", "last_name"])

        # Create StudentProfile (or attach to org) — ensure single profile per (user, org)

        sp, sp_created = StudentProfile.objects.get_or_create(
            user=user, organization=org,
            defaults={"current_classroom": classroom, "admission_no": admission_no}
        )
        if not sp_created:
            # Update on re-create attempts
            if classroom is not None:
                sp.current_classroom = classroom
            if admission_no is not None:
                sp.admission_no = admission_no
            sp.save()

        # Ensure org membership as STUDENT
        membership, _ = OrganizationMembership.objects.get_or_create(
            user=user, organization=org, role=OrganizationMembership.Role.STUDENT,
            defaults={"is_active": True}
        )

        # Apply status mapping
        _apply_status_to_user_membership(status_value, user, membership)
        user.save(update_fields=["is_active"])
        membership.save(update_fields=["is_active"])

        out = {
            "id": sp.id,
            "name": (user.get_full_name() or user.email),
            "email": user.email,
            "classroom": getattr(sp.current_classroom, "name", None),
            "admission_no": sp.admission_no,
            "status": _status_from_user_membership(user, membership),
        }
        return Response(StudentReadSerializer(out).data, status=status.HTTP_201_CREATED)

    @transaction.atomic
    def update(self, request, pk=None, *args, **kwargs):
        org, error = self._org_or_error()
        if error:
            return error

        try:
            sp = StudentProfile.objects.select_related("user").get(id=pk, organization=org)
        except StudentProfile.DoesNotExist:
            return Response({"detail": "Student not found."}, status=status.HTTP_404_NOT_FOUND)

        ser = StudentWriteSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        payload = ser.validated_data

        # Update user basic info
        name = (payload.get("name") or "").strip()
        first, last = (name.split(" ", 1) + [""])[:2]
        if first:
            sp.user.first_name = first
        if last != "":
            sp.user.last_name = last
        new_email = payload.get("email")
        if new_email and new_email.lower() != sp.user.email.lower():
            # prevent collision
            if User.objects.filter(email__iexact=new_email).exclude(id=sp.user_id).exists():
                return Response({"detail": "Email already in use by another user."},
                                status=status.HTTP_400_BAD_REQUEST)
            sp.user.email = new_email.lower()
        sp.user.save()

        # Update classroom
        classroom_name = payload.get("classroom")
        if classroom_name is not None:
            if classroom_name == "":
                sp.current_classroom = None
            else:
                classroom = Classroom.objects.filter(organization=org, name=classroom_name).first()
                if not classroom:
                    return Response({"detail": f"Classroom '{classroom_name}' not found."},
                                    status=status.HTTP_400_BAD_REQUEST)
                sp.current_classroom = classroom

        # Update admission number
        if "admissionNo" in payload:
            sp.admission_no = payload.get("admissionNo") or ""

        sp.save()

        # Update status via membership flags
        membership, _ = OrganizationMembership.objects.get_or_create(
            user=sp.user, organization=org, role=OrganizationMembership.Role.STUDENT,
            defaults={"is_active": True}
        )
        if "status" in payload:
            _apply_status_to_user_membership(payload["status"], sp.user, membership)
            sp.user.save(update_fields=["is_active"])
            membership.save(update_fields=["is_active"])

        out = {
            "id": sp.id,
            "name": (sp.user.get_full_name() or sp.user.email),
            "email": sp.user.email,
            "classroom": getattr(sp.current_classroom, "name", None),
            "admission_no": sp.admission_no,
            "status": _status_from_user_membership(sp.user, membership),
        }
        return Response(StudentReadSerializer(out).data)

    @transaction.atomic
    def destroy(self, request, pk=None, *args, **kwargs):
        """
        Delete is only allowed if the student is NOT enrolled in any course.
        """
        org, error = self._org_or_error()
        if error:
            return error

        try:
            sp = StudentProfile.objects.get(id=pk, organization=org)
        except StudentProfile.DoesNotExist:
            return Response({"detail": "Student not found."}, status=status.HTTP_404_NOT_FOUND)

        enrolled = Enrollment.objects.filter(student=sp).exists()
        if enrolled:
            return Response(
                {"detail": "Cannot delete student: the student is enrolled in one or more courses."},
                status=status.HTTP_400_BAD_REQUEST
            )
        student = sp.user
        sp.delete()
        student.delete()
        # (Optionally) also clean up membership role=STUDENT for this org if desired.
        OrganizationMembership.objects.filter(
            user_id=sp.user_id, organization=org, role=OrganizationMembership.Role.STUDENT
        ).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=["GET"], url_path="export")
    def export_csv(self, request, *args, **kwargs):
        """
        CSV export matching the UI's columns.
        """
        org, error = self._org_or_error()
        if error:
            return error

        qs = StudentProfile.objects.select_related("user", "current_classroom").filter(organization=org)
        memberships = {
            m.user_id: m
            for m in OrganizationMembership.objects.filter(
                organization=org, role=OrganizationMembership.Role.STUDENT,
                user_id__in=qs.values_list("user_id", flat=True)
            )
        }

        rows = [["Name", "Email", "Admission No", "Classroom", "Status"]]
        for sp in qs:
            user = sp.user
            membership = memberships.get(user.id)
            rows.append([
                (user.get_full_name() or user.email),
                user.email,
                sp.admission_no or "",
                getattr(sp.current_classroom, "name", "") or "",
                _status_from_user_membership(user, membership),
            ])

        # Stream as CSV response
        import csv
        from io import StringIO
        sio = StringIO()
        writer = csv.writer(sio)
        writer.writerows(rows)
        resp = HttpResponse(sio.getvalue(), content_type="text/csv")
        resp["Content-Disposition"] = 'attachment; filename="students.csv"'
        return resp
