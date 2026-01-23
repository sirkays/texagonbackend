import csv
import traceback
from datetime import datetime, timedelta
from decimal import Decimal
from io import StringIO, BytesIO
from typing import Any, Literal, Tuple
from calendar import monthrange

from django.db import transaction
from django.db.models import (
    Count,
    F,
    IntegerField,
    OuterRef,
    Q,
    Subquery,
    Sum,
    Value,
    Avg,
    DecimalField,
)
from django.db.models.functions import Coalesce
from django.http import HttpResponse
from django.utils import timezone

from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import (
    action,
    api_view,
    authentication_classes,
    permission_classes,
)
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework_api_key.permissions import HasAPIKey

from api.authentication import SessionTokenAuthentication

from academics.models import (
    Classroom,
    StudentProfile,
    TeacherProfile,
    Subject,
    ParentProfile,
    ParentChildLink,
)
from gamification.models import (
    Badge,
    BadgeAward,
    PointTransaction,
    Streak,
    AchievementDefinition,
)
from assessments.models import Submission, TestAttempt
from billing.models import (
    SubscriptionPayment,
    SubscriptionPlan,
    OrganizationSubscription,
    SubscriptionInvoice,
    Complaint,
    ComplaintResponse,
)
from learning.models import Course, Enrollment, Lesson, Module
from orgs.models import Organization, OrganizationMembership
from accounts.models import User

from .serializers import (
    ClassroomListSerializer,
    ClassroomWriteSerializer,
    StudentMiniSerializer,
    StudentReadSerializer,
    StudentWriteSerializer,
    TeacherListSerializer,
    TeacherWriteSerializer,
    TeacherDetailSerializer,
    ParentWriteSerializer,
    ParentDetailSerializer,
    ParentListSerializer,
    SubjectWriteSerializer,
    SubjectListItemSerializer,
)

from core.utils import (
    StatusLiteral,
    _resolve_org,
    _status_from_user_membership,
    _apply_status_to_user_membership,
    _avatar_url_for,
    _get_or_create_parent_membership,
    _is_admin,
    _is_org_admin_or_teacher,
    _get_ids_from_payload,
    _course_to_card_dict,
    _module_to_card_dict,
    _lesson_to_modal_row,
    _ach_to_dict,
    _badge_to_dict,
    _json_or_dict,
    _int,
    _member_display_name,
    _month_bounds,
    _parse_positive_int,
    _enrollment_to_dict,
    resolve_season,
)

# PDF generation
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas


@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def gamification_summary(request):
    """
    GET /api/admin/gamification/summary

    Headers:
      Authorization: Api-Key <YOUR_API_KEY>
      X-Session-Token: <session_token>
    """
    try:
        org, err = _resolve_org(request)
        if err:
            return err

        if not _is_org_admin_or_teacher(request, org):
            return Response({"detail": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

        # Total points awarded (sum of PointTransaction.points scoped to org students)
        org_students = StudentProfile.objects.filter(organization=org)
        total_points = (
            PointTransaction.objects
            .filter(student__in=org_students)
            .aggregate(t=Coalesce(Sum("points"), 0))
            .get("t", 0)
        )

        # Total badges earned
        badges_earned = (
            BadgeAward.objects
            .filter(student__in=org_students)
            .count()
        )

        # Active streaks (current_days > 0)
        active_streaks = (
            Streak.objects
            .filter(student__in=org_students, current_days__gt=0)
            .count()
        )

        # "Avg engagement" example: % of students with activity (streak updated) in last 7 days
        seven_days_ago = timezone.now().date() - timezone.timedelta(days=7)
        engaged = (
            Streak.objects
            .filter(student__in=org_students, last_activity__gte=seven_days_ago)
            .count()
        )
        total_students = max(1, org_students.count())
        avg_engagement_pct = int(round(100 * engaged / total_students))

        payload = {
            "totalPointsAwarded": int(total_points),
            "badgesEarned": int(badges_earned),
            "activeStreaks": int(active_streaks),
            "avgEngagement": avg_engagement_pct,
        }
        return Response(payload)

    except Exception as e:
        traceback.print_exc()
        return Response({"detail": "Unexpected error", "error": str(e)},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ----------------------------------------
# Badges: list/create and update
# ----------------------------------------

@api_view(["GET", "POST"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def badges_view(request):
    """
    GET  /api/admin/gamification/badges
    POST /api/admin/gamification/badges

    Headers:
      Authorization: Api-Key <YOUR_API_KEY>
      X-Session-Token: <session_token>

    Body (POST):
      {
        "name": "Helping Hand",
        "icon_name": "medal",
        "color": "bg-emerald-500",
        "points": 60,
        "criteria": "...",
        "rules": {...}  # optional JSON
      }
    """
    try:
        org, err = _resolve_org(request)
        if err:
            return err

        if request.method == "GET":
            if not _is_org_admin_or_teacher(request, org):
                return Response({"detail": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

            qs = Badge.objects.filter(organization=org).order_by("-id")
            return Response([_badge_to_dict(b) for b in qs])

        # POST (create)
        if not _is_org_admin_or_teacher(request, org):
            return Response({"detail": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

        data = request.data or {}
        b = Badge.objects.create(
            organization=org,
            name=data.get("name", "").strip(),
            icon_name=data.get("icon_name", "medal").strip(),
            color=data.get("color", "bg-gray-500").strip(),
            points=_int(data.get("points", 0)),
            criteria=data.get("criteria", "").strip(),
            rules=_json_or_dict(data.get("rules") or {}),
        )
        return Response(_badge_to_dict(b), status=status.HTTP_201_CREATED)

    except Exception as e:
        traceback.print_exc()
        return Response({"detail": "Unexpected error", "error": str(e)},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["PATCH"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def badge_detail(request, badge_id: int):
    """
    PATCH /api/admin/gamification/badges/<badge_id>

    Headers:
      Authorization: Api-Key <YOUR_API_KEY>
      X-Session-Token: <session_token>
    """
    try:
        org, err = _resolve_org(request)
        if err:
            return err

        if not _is_org_admin_or_teacher(request, org):
            return Response({"detail": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

        b = Badge.objects.filter(organization=org, id=badge_id).first()
        if not b:
            return Response({"detail": "Badge not found."}, status=status.HTTP_404_NOT_FOUND)

        data = request.data or {}
        if "name" in data:
            b.name = (data.get("name") or "").strip()
        if "icon_name" in data:
            b.icon_name = (data.get("icon_name") or "").strip()
        if "color" in data:
            b.color = (data.get("color") or "").strip()
        if "points" in data:
            b.points = _int(data.get("points"))
        if "criteria" in data:
            b.criteria = (data.get("criteria") or "").strip()
        if "rules" in data:
            b.rules = _json_or_dict(data.get("rules"))

        b.save()
        return Response(_badge_to_dict(b))

    except Exception as e:
        traceback.print_exc()
        return Response({"detail": "Unexpected error", "error": str(e)},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR)

# ----------------------------------------
# Achievements: list/create and update
# ----------------------------------------

@api_view(["GET", "POST"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def achievements_view(request):
    """
    GET  /api/admin/gamification/achievements
    POST /api/admin/gamification/achievements

    Body (POST):
      {
        "code": "streak_champion",
        "title": "Streak Champion",
        "description": "Maintain a 30-day learning streak",
        "icon": "zap",
        "category": "Consistency",
        "target_value": 30,
        "points": 200,
        "is_active": true
      }
    """
    try:
        org, err = _resolve_org(request)
        if err:
            return err

        if request.method == "GET":
            if not _is_org_admin_or_teacher(request, org):
                return Response({"detail": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

            qs = AchievementDefinition.objects.filter(
                Q(organization=org) | Q(organization__isnull=True)
            ).order_by("code")
            return Response([_ach_to_dict(a) for a in qs])

        # POST
        if not _is_org_admin_or_teacher(request, org):
            return Response({"detail": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

        data = request.data or {}
        a = AchievementDefinition.objects.create(
            organization=org,
            code=data.get("code", "").strip(),
            title=data.get("title", "").strip(),
            description=data.get("description", "").strip(),
            icon=data.get("icon", "star").strip(),
            category=data.get("category", "General").strip(),
            target_value=(None if data.get("target_value") in ("", None) else _int(data.get("target_value"))),
            points=_int(data.get("points", 0)),
            is_active=bool(data.get("is_active", True)),
        )
        return Response(_ach_to_dict(a), status=status.HTTP_201_CREATED)

    except Exception as e:
        traceback.print_exc()
        return Response({"detail": "Unexpected error", "error": str(e)},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["PATCH"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def achievement_detail(request, achievement_id: int):
    """
    PATCH /api/admin/gamification/achievements/<achievement_id>
    """
    try:
        org, err = _resolve_org(request)
        if err:
            return err

        if not _is_org_admin_or_teacher(request, org):
            return Response({"detail": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

        a = AchievementDefinition.objects.filter(
            (Q(organization=org) | Q(organization__isnull=True)),
            id=achievement_id,
        ).first()
        if not a:
            return Response({"detail": "Achievement not found."}, status=status.HTTP_404_NOT_FOUND)

        data = request.data or {}
        if "code" in data:
            a.code = (data.get("code") or "").strip()
        if "title" in data:
            a.title = (data.get("title") or "").strip()
        if "description" in data:
            a.description = (data.get("description") or "").strip()
        if "icon" in data:
            a.icon = (data.get("icon") or "").strip()
        if "category" in data:
            a.category = (data.get("category") or "").strip()
        if "target_value" in data:
            raw = data.get("target_value")
            a.target_value = None if raw in ("", None) else _int(raw)
        if "points" in data:
            a.points = _int(data.get("points"))
        if "is_active" in data:
            a.is_active = bool(data.get("is_active"))

        a.save()
        return Response(_ach_to_dict(a))

    except Exception as e:
        traceback.print_exc()
        return Response({"detail": "Unexpected error", "error": str(e)},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR)

# ----------------------------------------
# Leaderboard
# ----------------------------------------

@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def gamification_leaderboard(request):
    """
    GET /api/admin/gamification/leaderboard

    Returns: [{rank, student, points, badges, streak}]
    """
    try:
        org, err = _resolve_org(request)
        if err:
            return err

        if not _is_org_admin_or_teacher(request, org):
            return Response({"detail": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

        students = StudentProfile.objects.filter(organization=org)

        # Sum of points per student
        points_by_student = (
            PointTransaction.objects
            .filter(student__in=students)
            .values("student_id")
            .annotate(points=Coalesce(Sum("points"), 0))
        )
        points_map = {row["student_id"]: int(row["points"]) for row in points_by_student}

        # Badge counts
        badge_counts = (
            BadgeAward.objects
            .filter(student__in=students)
            .values("student_id")
            .annotate(c=Count("id"))
        )
        badges_map = {row["student_id"]: int(row["c"]) for row in badge_counts}

        # Streaks
        streaks = Streak.objects.filter(student__in=students)
        streak_map = {s.student_id: int(s.current_days or 0) for s in streaks}

        # Compose + sort
        rows = []
        for s in students.select_related("user"):
            student_name = (s.user.get_full_name() or s.user.email or f"Student {s.id}")
            rows.append({
                "studentId": s.id,
                "student": student_name,
                "points": points_map.get(s.id, 0),
                "badges": badges_map.get(s.id, 0),
                "streak": streak_map.get(s.id, 0),
            })

        rows.sort(key=lambda r: (-r["points"], -r["badges"], -r["streak"], r["student"]))
        # Add rank
        for idx, r in enumerate(rows, start=1):
            r["rank"] = idx

        return Response(rows[:50])  # cap to top 50

    except Exception as e:
        traceback.print_exc()
        return Response({"detail": "Unexpected error", "error": str(e)},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR)




@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def invoice_pdf(request, invoice_id: int):
    """
    GET /api/admin/billing/invoices/<invoice_id>/pdf
    Returns an actual PDF file download.
    """
    org, err = _resolve_org(request)
    if err:
        return err

    if not _is_org_admin_or_teacher(request, org):
        return Response({"detail": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

    inv = (
        SubscriptionInvoice.objects
        .select_related("subscription__plan", "subscription__organization", "organization_membership__user")
        .filter(subscription__organization=org, id=invoice_id)
        .first()
    )
    if not inv:
        return Response({"detail": "Invoice not found."}, status=status.HTTP_404_NOT_FOUND)

    plan = getattr(inv.subscription, "plan", None)

    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4

    # Header
    c.setFont("Helvetica-Bold", 16)
    c.drawString(40, height - 60, "Invoice")

    c.setFont("Helvetica", 10)
    c.drawString(40, height - 85, f"Invoice No: {inv.number}")
    c.drawString(40, height - 100, f"Status: {inv.status}")
    c.drawString(40, height - 115, f"Issued: {inv.issued_at.strftime('%Y-%m-%d %H:%M') if inv.issued_at else ''}")
    c.drawString(40, height - 130, f"Due: {inv.due_at.strftime('%Y-%m-%d %H:%M') if inv.due_at else ''}")

    # Bill to
    c.setFont("Helvetica-Bold", 11)
    c.drawString(40, height - 160, "Bill To")
    c.setFont("Helvetica", 10)
    c.drawString(40, height - 175, _member_display_name(inv.organization_membership))

    # Line item
    y = height - 220
    c.setFont("Helvetica-Bold", 11)
    c.drawString(40, y, "Item")
    c.drawString(350, y, "Amount")

    c.setFont("Helvetica", 10)
    y -= 20
    title = f"{plan.name if plan else 'Plan'} Subscription"
    c.drawString(40, y, title)
    c.drawRightString(520, y, f"{inv.currency} {Decimal(inv.amount):.2f}")

    # Total
    y -= 50
    c.setFont("Helvetica-Bold", 12)
    c.drawString(40, y, "Total")
    c.drawRightString(520, y, f"{inv.currency} {Decimal(inv.amount):.2f}")

    c.showPage()
    c.save()

    pdf = buf.getvalue()
    buf.close()

    resp = HttpResponse(pdf, content_type="application/pdf")
    resp["Content-Disposition"] = f'attachment; filename="{inv.number}.pdf"'
    return resp





@api_view(["GET", "POST"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def billing_plans(request):
    org, err = _resolve_org(request)
    if err:
        return err
    if not _is_org_admin_or_teacher(request, org):
        return Response({"detail": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

    if request.method == "GET":
        qs = SubscriptionPlan.objects.all().order_by("id")
        return Response([
            {
                "id": p.id,
                "name": p.name,
                "price": f"{p.price:.2f}",
                "billing_period": p.billing_period,
                "student_limit": p.student_limit,
                "features": p.features or [],
            }
            for p in qs
        ])

    # POST create
    data = request.data or {}
    name = (data.get("name") or "").strip()
    price = data.get("price")
    billing_period = str(data.get("billing_period") or "30")
    student_limit = int(data.get("student_limit") or 0)
    features = data.get("features") or []

    if not name:
        return Response({"detail": "name is required"}, status=status.HTTP_400_BAD_REQUEST)

    p = SubscriptionPlan.objects.create(
        name=name,
        price=price,
        billing_period=billing_period,
        student_limit=student_limit,
        features=features,
    )
    return Response({
        "id": p.id,
        "name": p.name,
        "price": f"{p.price:.2f}",
        "billing_period": p.billing_period,
        "student_limit": p.student_limit,
        "features": p.features or [],
    }, status=status.HTTP_201_CREATED)



@api_view(["PATCH"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def billing_plan_update(request, plan_id: int):
    org, err = _resolve_org(request)
    if err:
        return err
    if not _is_org_admin_or_teacher(request, org):
        return Response({"detail": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

    p = SubscriptionPlan.objects.filter(id=plan_id).first()
    if not p:
        return Response({"detail": "Plan not found."}, status=status.HTTP_404_NOT_FOUND)

    data = request.data or {}
    if "name" in data: p.name = (data.get("name") or "").strip()
    if "price" in data: p.price = data.get("price")
    if "billing_period" in data: p.billing_period = str(data.get("billing_period") or p.billing_period)
    if "student_limit" in data: p.student_limit = int(data.get("student_limit") or 0)
    if "features" in data: p.features = data.get("features") or []

    p.save()
    return Response({
        "id": p.id,
        "name": p.name,
        "price": f"{p.price:.2f}",
        "billing_period": p.billing_period,
        "student_limit": p.student_limit,
        "features": p.features or [],
    })

@api_view(["POST"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def billing_plan_activate(request, plan_id: int):
    org, err = _resolve_org(request)
    if err:
        return err
    if not _is_org_admin_or_teacher(request, org):
        return Response({"detail": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

    plan = SubscriptionPlan.objects.filter(id=plan_id).first()
    if not plan:
        return Response({"detail": "Plan not found."}, status=status.HTTP_404_NOT_FOUND)

    # You may want to pick who pays (parent membership). For org-level activation,
    # we can keep invoice.organization_membership = None.
    with transaction.atomic():
        # expire active subs
        OrganizationSubscription.objects.filter(
            organization=org,
            status=OrganizationSubscription.Status.ACTIVE,
        ).update(status=OrganizationSubscription.Status.EXPIRED)

        start = timezone.now().date()
        days = int(plan.billing_period or "30")
        end = start + timedelta(days=days)

        sub = OrganizationSubscription.objects.create(
            organization=org,
            plan=plan,
            start_date=start,
            end_date=end,
            status=OrganizationSubscription.Status.ACTIVE,
            auto_renew=True,
        )

        inv = SubscriptionInvoice.objects.create(
            subscription=sub,
            amount=plan.price,
            currency="NGN",
            status=SubscriptionInvoice.Status.OPEN,
            due_at=timezone.now() + timedelta(days=3),
        )

    return Response({
        "subscription": {
            "id": sub.id,
            "status": sub.status,
            "start_date": str(sub.start_date),
            "end_date": str(sub.end_date),
            "plan": {"id": plan.id, "name": plan.name},
        },
        "invoice": {"id": inv.id, "number": inv.number, "status": inv.status},
    }, status=status.HTTP_201_CREATED)





@api_view(["GET", "PATCH"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def admin_complaint_detail(request, complaint_id):
    org, err = _resolve_org(request)
    if err:
        return err
    if not _is_org_admin_or_teacher(request, org):
        return Response({"detail": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

    c = Complaint.objects.filter(id=complaint_id).first()
    if not c:
        return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "PATCH":
        data = request.data or {}
        if "status" in data: c.status = data["status"]
        if "priority" in data: c.priority = data["priority"]
        c.save()

    responses = list(c.responses.select_related("author").order_by("created_at"))
    return Response({
        "id": str(c.id),
        "code": c.code,
        "title": c.title,
        "description": c.description,
        "status": c.status,
        "priority": c.priority,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "transaction_id": c.transaction_identifier,
        "responses": [
            {
                "id": str(r.id),
                "role": r.role,
                "author_name": r.author_name,
                "message": r.message,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in responses
        ]
    })


@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def admin_complaints(request):
    org, err = _resolve_org(request)
    if err:
        return err
    if not _is_org_admin_or_teacher(request, org):
        return Response({"detail": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

    search = (request.query_params.get("search") or "").strip()
    status_f = (request.query_params.get("status") or "").strip()

    page = max(int(request.query_params.get("page") or 1), 1)
    page_size = min(max(int(request.query_params.get("page_size") or 10), 1), 50)

    qs = Complaint.objects.all().order_by("-created_at")

    # If your Complaint should be org-scoped, add your org filter here.
    # Example (if you add organization FK later):
    # qs = qs.filter(organization=org)

    if status_f:
        qs = qs.filter(status=status_f)

    if search:
        qs = qs.filter(
            Q(code__icontains=search)
            | Q(title__icontains=search)
            | Q(description__icontains=search)
        )

    total = qs.count()
    start = (page - 1) * page_size
    rows = list(qs[start:start + page_size])

    payload = [
        {
            "id": str(c.id),
            "code": c.code,
            "title": c.title,
            "status": c.status,
            "priority": c.priority,
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "responses_count": c.responses_count,
            "transaction_id": c.transaction_identifier,
        }
        for c in rows
    ]

    return Response({"results": payload, "pagination": {"page": page, "page_size": page_size, "total": total}})



@api_view(["POST"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def admin_complaint_add_response(request, complaint_id):
    org, err = _resolve_org(request)
    if err:
        return err
    if not _is_org_admin_or_teacher(request, org):
        return Response({"detail": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

    c = Complaint.objects.filter(id=complaint_id).first()
    if not c:
        return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

    msg = (request.data.get("message") or "").strip()
    if not msg:
        return Response({"detail": "message is required"}, status=status.HTTP_400_BAD_REQUEST)

    r = ComplaintResponse.objects.create(
        complaint=c,
        author=request.user,
        author_name=getattr(request.user, "get_full_name", lambda: "")() or request.user.email,
        role=ComplaintResponse.Role.ADMIN,
        message=msg,
    )
    return Response({
        "id": str(r.id),
        "role": r.role,
        "author_name": r.author_name,
        "message": r.message,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }, status=status.HTTP_201_CREATED)


@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def billing_dashboard(request):
    """
    GET /api/admin/billing/dashboard

    Headers:
      Authorization: Api-Key <YOUR_API_KEY>
      X-Session-Token: <session_token>

    Query:
      invoices_page (int, default 1)
      invoices_page_size (int, default 10, max 50)
      invoices_search (str)
    """
    try:
        # ---------- Resolve org & permission ----------
        org, err = _resolve_org(request)
        if err:
            return err

        if not _is_org_admin_or_teacher(request, org):
            return Response({"detail": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

        # ---------- Local helpers ----------
        DEC_OUT = DecimalField(max_digits=18, decimal_places=2)

        def dec_sum(qs, field_name: str) -> Decimal:
            """Safe Decimal SUM with Coalesce and explicit output_field."""
            return (
                qs.aggregate(total=Coalesce(Sum(field_name), Value(0), output_field=DEC_OUT))["total"]
                or Decimal("0")
            )

        def member_display_name(membership: OrganizationMembership | None) -> str:
            if not membership:
                return ""
            u = membership.user
            full = (getattr(u, "get_full_name", lambda: "")() or "").strip()
            return full or u.email or f"user-{u.pk}"

        def month_bounds(dt=None):
            now = dt or timezone.now()
            y, m = now.year, now.month
            first = timezone.make_aware(datetime(y, m, 1, 0, 0, 0))
            last_day = monthrange(y, m)[1]
            last = timezone.make_aware(datetime(y, m, last_day, 23, 59, 59))
            return first, last

        # ---------- Base querysets ----------
        inv_qs = SubscriptionInvoice.objects.filter(subscription__organization=org)

        # ---------- Stats ----------
        start_m, end_m = month_bounds()

        monthly_paid_amt = dec_sum(
            inv_qs.filter(
                status=SubscriptionInvoice.Status.PAID,
                issued_at__gte=start_m,
                issued_at__lte=end_m,
            ),
            "amount",
        )

        active_subscriptions = OrganizationSubscription.objects.filter(
            organization=org,
            status=OrganizationSubscription.Status.ACTIVE,
        ).count()

        pending_qs = inv_qs.filter(
            Q(status=SubscriptionInvoice.Status.OPEN) | Q(status=SubscriptionInvoice.Status.ACTIVE)
        )
        pending_count = pending_qs.count()
        pending_total = dec_sum(pending_qs, "amount")

        # Collection rate over last 60 days
        since = timezone.now() - timezone.timedelta(days=60)
        window = inv_qs.filter(issued_at__gte=since)
        paid_w = window.filter(status=SubscriptionInvoice.Status.PAID)
        due_w = window.exclude(status=SubscriptionInvoice.Status.VOID)

        paid_amt = dec_sum(paid_w, "amount")
        due_amt = dec_sum(due_w, "amount")
        amount_pct = float(paid_amt / due_amt) if due_amt else 0.0

        paid_cnt = paid_w.count()
        due_cnt = due_w.count()
        count_pct = float(paid_cnt / due_cnt) if due_cnt else 0.0

        # Prefer the first seen currency; fallback NGN
        currency = inv_qs.values_list("currency", flat=True).order_by().first() or "NGN"

        stats = {
            "monthly_revenue_amount": f"{monthly_paid_amt:.2f}",
            "monthly_revenue_currency": currency,
            "active_subscriptions": active_subscriptions,
            "pending_invoices_count": pending_count,
            "pending_invoices_total": f"{pending_total:.2f}",
            "collection_rate": {
                "amount_pct": round(amount_pct, 4),
                "count_pct": round(count_pct, 4),
            },
        }

        # ---------- Plans + active counts ----------
        plans = (
            SubscriptionPlan.objects
            .filter(subscriptions__organization=org)
            .distinct()
            .annotate(
                active_subscriptions=Count(
                    "subscriptions",
                    filter=Q(subscriptions__status=OrganizationSubscription.Status.ACTIVE),
                    distinct=True,
                )
            )
            .values("id", "name", "price", "billing_period", "student_limit", "active_subscriptions")
        )
        plans_payload = [
            {
                "id": p["id"],
                "name": p["name"],
                "price": f'{p["price"]:.2f}',
                "billing_period": p["billing_period"],
                "student_limit": p["student_limit"],
                "active_subscriptions": p["active_subscriptions"],
            }
            for p in plans
        ]

        # ---------- Recent invoices (search + pagination) ----------
        page = max(int(request.query_params.get("invoices_page") or 1), 1)
        page_size = min(max(int(request.query_params.get("invoices_page_size") or 10), 1), 50)
        search = (request.query_params.get("invoices_search") or "").strip()

        recent_qs = inv_qs.select_related("organization_membership__user").order_by("-issued_at", "-id")
        if search:
            recent_qs = recent_qs.filter(
                Q(number__icontains=search) |
                Q(organization_membership__user__email__icontains=search) |
                Q(organization_membership__user__first_name__icontains=search) |
                Q(organization_membership__user__last_name__icontains=search)
            )

        total = recent_qs.count()
        start = (page - 1) * page_size
        rows = list(recent_qs[start:start + page_size])

        recent_payload = [
            {
                "id": inv.id,
                "number": inv.number,
                "parent": member_display_name(inv.organization_membership),
                "amount": f"{inv.amount:.2f}",
                "currency": inv.currency,
                "status": inv.status,
                "issuedAt": inv.issued_at.isoformat() if inv.issued_at else None,
                "dueAt": inv.due_at.isoformat() if inv.due_at else None,
            }
            for inv in rows
        ]

        return Response({
            "stats": stats,
            "plans": plans_payload,
            "recent_invoices": recent_payload,
            "pagination": {"page": page, "page_size": page_size, "total": total},
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return Response({"detail": "Unexpected error", "error": str(e)},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def invoice_detail(request, invoice_id: int):
    """
    GET /api/admin/billing/invoices/<invoice_id>

    Headers:
      Authorization: Api-Key <YOUR_API_KEY>
      X-Session-Token: <session_token>
    """
    try:
        org, err = _resolve_org(request)
        if err:
            return err

        if not _is_org_admin_or_teacher(request, org):
            return Response({"detail": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)
        
        inv = (
            SubscriptionInvoice.objects
            .select_related("subscription__plan", "subscription__organization", "organization_membership__user")
            .filter(subscription__organization=org, id=invoice_id)
            .first()
        )
        if not inv:
            return Response({"detail": "Invoice not found."}, status=status.HTTP_404_NOT_FOUND)

        plan = getattr(inv.subscription, "plan", None)
        payload = {
            "id": inv.id,
            "number": inv.number,
            "parent": _member_display_name(inv.organization_membership),
            "amount": f"{inv.amount:.2f}",
            "currency": inv.currency,
            "status": inv.status,
            "issuedAt": inv.issued_at.isoformat() if inv.issued_at else None,
            "dueAt": inv.due_at.isoformat() if inv.due_at else None,
            "items": [
                {
                    "title": f"{plan.name if plan else 'Plan'} Subscription",
                    "description": "Monthly billing period",
                    "amount": f"{inv.amount:.2f}",
                }
            ],
            "payment_info": None,
        }

        latest_payment = inv.payments.order_by("-paid_at").first() if hasattr(inv, "payments") else None
        if latest_payment:
            payload["payment_info"] = {
                "paid_at": latest_payment.paid_at.isoformat() if latest_payment.paid_at else None,
                "transaction_id": latest_payment.transaction_id,
                "method": latest_payment.method,
                "status": latest_payment.status,
            }

        return Response(payload)

    except Exception as e:
        traceback.print_exc()
        return Response({"detail": "Unexpected error", "error": str(e)},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR)



# ---------- endpoints ----------
@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def modules_list(request):
    """
    GET /api/admin/modules
    Auth:
      - Authorization: Api-Key <YOUR_API_KEY>
      - X-Session-Key: <session_key>

    Query Params:
      - search: str (matches module name, course name, category name)
      - course_id: int                     # filter by one course
      - course_ids: comma-separated ints   # filter by many courses, e.g. "1,2,3"
      - course: str                        # filter by course name (icontains)
      - difficulty: BEGINNER|INTERMEDIATE|ADVANCED
      - status: active|inactive
      - page: int (default 1)
      - page_size: int (default 20, max 100)
    """
    try:
        org, err = _resolve_org(request)
        if err:
            return err

        if not _is_org_admin_or_teacher(request, org):
            return Response({"detail": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

        search = (request.query_params.get("search") or "").strip()
        difficulty = (request.query_params.get("difficulty") or "").strip().upper()
        status_filter = (request.query_params.get("status") or "").strip().lower()

        # ---- COURSE FILTERS ----
        course_id = request.query_params.get("course_id")
        course_ids_raw = (request.query_params.get("course_ids") or "").strip()
        course_name_q = (request.query_params.get("course") or "").strip()

        # pagination
        page = max(int(request.query_params.get("page") or 1), 1)
        page_size = min(max(int(request.query_params.get("page_size") or 20), 1), 100)

        qs = (
            Module.objects
            .filter(course__organization=org)
            .select_related("course", "category")
            .annotate(
                lessons_count=Count("lessons", distinct=True),
                duration_minutes=Coalesce("estimated_duration_in_minutes", Value(0), output_field=IntegerField()),
            )
        )

        # text search
        if search:
            qs = qs.filter(
                Q(name__icontains=search) |
                Q(course__name__icontains=search) |
                Q(category__name__icontains=search)
            )

        # filter by a single course id
        if course_id:
            try:
                qs = qs.filter(course_id=int(course_id))
            except ValueError:
                pass

        # filter by many course ids
        if course_ids_raw:
            try:
                ids = [int(x) for x in course_ids_raw.split(",") if x.strip().isdigit()]
                if ids:
                    qs = qs.filter(course_id__in=ids)
            except Exception:
                pass

        # filter by course name
        if course_name_q:
            qs = qs.filter(course__name__icontains=course_name_q)

        # difficulty + status
        if difficulty in {"BEGINNER", "INTERMEDIATE", "ADVANCED"}:
            qs = qs.filter(difficulty=difficulty)
        if status_filter in {"active", "inactive"}:
            qs = qs.filter(active=(status_filter == "active"))

        total = qs.count()
        start = (page - 1) * page_size
        rows = list(qs.order_by("course__name", "order")[start:start + page_size])

        def _module_to_card_dict(m):
            return {
                "id": m.id,
                "name": m.name,
                "course": getattr(m.course, "name", ""),
                "order": m.order,
                "difficulty": m.difficulty,
                "lessons": m.lessons_count or 0,
                "duration": int(m.duration_minutes or 0),
                "category": getattr(m.category, "name", "") if m.category_id else "",
                "active": bool(m.active),
            }

        data = [_module_to_card_dict(m) for m in rows]

        # Optional: return a compact list of available courses (for a filter dropdown)
        courses_filter = (
            Module.objects
            .filter(course__organization=org)
            .values("course_id", "course__name")
            .distinct()
            .order_by("course__name")
        )
        courses_list = [
            {"id": c["course_id"], "name": c["course__name"]}
            for c in courses_filter
        ]

        return Response({
            "results": data,
            "pagination": {"page": page, "page_size": page_size, "total": total},
            "filters": {"courses": courses_list},
        })

    except Exception as e:
        traceback.print_exc()
        return Response(
            {"detail": "An unexpected error occurred.", "error": str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def module_lessons(request, module_id: int):
    """
    GET /api/admin/modules/<module_id>/lessons
    Auth:
      - Header: Authorization: Api-Key <YOUR_API_KEY>
      - Header: X-Session-Key: <session_key>

    Returns lessons for the given module, shaped for your modal.
    """
    try:
        org, err = _resolve_org(request)
        if err:
            return err
        if not _is_org_admin_or_teacher(request, org):
            return Response({"detail": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)
        # Ensure the module belongs to the caller's org
        module = (
            Module.objects
            .select_related("course")
            .filter(id=module_id, course__organization=org)
            .first()
        )
        if not module:
            return Response({"detail": "Module not found."}, status=status.HTTP_404_NOT_FOUND)
        lessons_qs = (
            Lesson.objects
            .filter(module=module)
            .order_by("order", "id")
        )

        lessons = [_lesson_to_modal_row(l, idx) for idx, l in enumerate(lessons_qs, start=1)]
        # Extra header info used by your modal header
        payload = {
            "module": {
                "id": module.id,
                "name": module.name,
                "order": module.order,
                "difficulty": module.difficulty,
                "lessons": len(lessons),
            },
            "lessons": lessons,
        }
        return Response(payload)

    except Exception as e:
        traceback.print_exc()
        return Response(
            {"detail": "An unexpected error occurred.", "error": str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def courses_list(request):
    try:
        org, err = _resolve_org(request)
        if err:
            return err

        if not _is_org_admin_or_teacher(request, org):
            return Response({"detail": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

        search = (request.query_params.get("search") or "").strip()
        page = max(int(request.query_params.get("page") or 1), 1)
        page_size = min(max(int(request.query_params.get("page_size") or 20), 1), 100)
        status_filter = (request.query_params.get("status") or "").strip().lower()

        qs = (Course.objects
              .filter(organization=org)
              .select_related("subject", "classroom", "teacher__user")
              .annotate(
                  students_count=Count("enrollments", distinct=True),
                  modules_count=Count("modules", distinct=True),
                  avg_progress=Avg("enrollments__progress_pct"),
              ))

        if search:
            qs = qs.filter(
                Q(name__icontains=search) |
                Q(subject__name__icontains=search) |
                Q(teacher__user__first_name__icontains=search) |
                Q(teacher__user__last_name__icontains=search) |
                Q(teacher__user__email__icontains=search)
            )

        if status_filter in {"active", "inactive"}:
            qs = qs.filter(is_active=(status_filter == "active"))

        total = qs.count()
        start = (page - 1) * page_size
        rows = list(qs.order_by("-id")[start:start + page_size])

        data = [_course_to_card_dict(c) for c in rows]
        return Response({
            "results": data,
            "pagination": {"page": page, "page_size": page_size, "total": total}
        })
    except Exception as e:
        print("Error in courses_list:", e)
        traceback.print_exc()
        return Response({"detail": "An unexpected error occurred.", "error": str(e)},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def courses_stats_header(request):
    try:
        org, err = _resolve_org(request)
        if err:
            return err

        if not _is_org_admin_or_teacher(request, org):
            return Response({"detail": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

        active_courses = Course.objects.filter(organization=org, is_active=True).count()
        total_enrollments = Enrollment.objects.filter(course__organization=org).count()

        return Response({
            "active_courses": active_courses,
            "total_enrollments": total_enrollments,
        })
    except Exception as e:
        print("Error in courses_stats_header:", e)
        traceback.print_exc()
        return Response({"detail": "An unexpected error occurred.", "error": str(e)},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def course_detail(request, course_id: int):
    try:
        org, err = _resolve_org(request)
        if err:
            return err

        if not _is_org_admin_or_teacher(request, org):
            return Response({"detail": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

        c = (Course.objects
             .filter(id=course_id, organization=org)
             .select_related("subject", "classroom", "teacher__user")
             .annotate(
                 students_count=Count("enrollments", distinct=True),
                 modules_count=Count("modules", distinct=True),
                 avg_progress=Avg("enrollments__progress_pct"),
             ).first())

        if not c:
            return Response({"detail": "Course not found."}, status=status.HTTP_404_NOT_FOUND)

        return Response(_course_to_card_dict(c))
    except Exception as e:
        print("Error in course_detail:", e)
        traceback.print_exc()
        return Response({"detail": "An unexpected error occurred.", "error": str(e)},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["POST"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def course_create(request):
    try:
        org, err = _resolve_org(request)
        if err:
            return err

        if not _is_org_admin_or_teacher(request, org):
            return Response({"detail": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

        data = request.data or {}
        name = (data.get("name") or "").strip()
        if not name:
            return Response({"detail": "name is required."}, status=status.HTTP_400_BAD_REQUEST)

        subject_id, classroom_id, teacher_id = _get_ids_from_payload(data, org)
        if not subject_id or not classroom_id or not teacher_id:
            return Response({"detail": "subject, classroom and teacher are required."},
                            status=status.HTTP_400_BAD_REQUEST)

        exists = Course.objects.filter(
            organization=org,
            subject_id=subject_id,
            classroom_id=classroom_id,
            teacher_id=teacher_id
        ).exists()
        if exists:
            return Response({"detail": "A course with this subject, classroom and teacher already exists."},
                            status=status.HTTP_400_BAD_REQUEST)

        c = Course.objects.create(
            organization=org,
            name=name,
            subject_id=subject_id,
            classroom_id=classroom_id,
            teacher_id=teacher_id,
            description=data.get("description", ""),
            is_active=bool(data.get("is_active", True)),
            course_type=data.get("course_type", "public"),
        )

        c = (Course.objects
             .filter(pk=c.pk)
             .select_related("subject", "classroom", "teacher__user")
             .annotate(
                 students_count=Count("enrollments", distinct=True),
                 modules_count=Count("modules", distinct=True),
                 avg_progress=Avg("enrollments__progress_pct"),
             ).first())

        return Response(_course_to_card_dict(c), status=status.HTTP_201_CREATED)
    except Exception as e:
        print("Error in course_create:", e)
        traceback.print_exc()
        return Response({"detail": "An unexpected error occurred.", "error": str(e)},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["PATCH", "PUT"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def course_update(request, course_id: int):
    try:
        org, err = _resolve_org(request)
        if err:
            return err

        if not _is_org_admin_or_teacher(request, org):
            return Response({"detail": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

        c = Course.objects.filter(id=course_id, organization=org).first()
        if not c:
            return Response({"detail": "Course not found."}, status=status.HTTP_404_NOT_FOUND)

        data = request.data or {}

        name = data.get("name")
        if name is not None:
            c.name = name.strip()

        if any(k in data for k in ["subject_id", "subject", "classroom_id", "classroom", "teacher_id", "teacher"]):
            subject_id, classroom_id, teacher_id = _get_ids_from_payload(data, org)
            c.subject_id = subject_id or c.subject_id
            c.classroom_id = classroom_id or c.classroom_id
            c.teacher_id = teacher_id or c.teacher_id

            dup = Course.objects.filter(
                organization=org,
                subject_id=c.subject_id,
                classroom_id=c.classroom_id,
                teacher_id=c.teacher_id
            ).exclude(pk=c.pk).exists()
            if dup:
                return Response({"detail": "A course with this subject, classroom and teacher already exists."},
                                status=status.HTTP_400_BAD_REQUEST)

        if "description" in data:
            c.description = data.get("description") or ""
        if "is_active" in data:
            c.is_active = bool(data.get("is_active"))
        if "usage_type" in data:
            c.course_type = data.get("usage_type") or c.course_type

        c.save()

        c = (Course.objects
             .filter(pk=c.pk)
             .select_related("subject", "classroom", "teacher__user")
             .annotate(
                 students_count=Count("enrollments", distinct=True),
                 modules_count=Count("modules", distinct=True),
                 avg_progress=Avg("enrollments__progress_pct"),
             ).first())

        return Response(_course_to_card_dict(c))
    except Exception as e:
        print("Error in course_update:", e)
        traceback.print_exc()
        return Response({"detail": "An unexpected error occurred.", "error": str(e)},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["DELETE"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def course_delete(request, course_id: int):
    try:
        org, err = _resolve_org(request)
        if err:
            return err

        if not _is_org_admin_or_teacher(request, org):
            return Response({"detail": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

        c = Course.objects.filter(id=course_id, organization=org).first()
        if not c:
            return Response({"detail": "Course not found."}, status=status.HTTP_404_NOT_FOUND)

        name = c.name
        if c.enrollments.all().count() == 0:
            c.delete()
        else:
            return Response({"detail": "Course cannot be deleted, already enrolled."},
                            status=status.HTTP_400_BAD_REQUEST)

        return Response({"detail": f"{name} deleted."}, status=status.HTTP_200_OK)
    except Exception as e:
        print("Error in course_delete:", e)
        traceback.print_exc()
        return Response({"detail": "An unexpected error occurred.", "error": str(e)},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def course_form_options(request):
    try:
        org, err = _resolve_org(request)
        if err:
            return err

        if not _is_org_admin_or_teacher(request, org):
            return Response({"detail": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

        subjects = list(Subject.objects.filter(organization=org).values("id", "name"))
        classrooms = list(Classroom.objects.filter(organization=org).values("id", "name"))
        teachers = [{
            "id": t.id,
            "name": (t.user.get_full_name() or t.user.email),
            "email": t.user.email,
        } for t in TeacherProfile.objects.filter(organization=org).select_related("user")]

        return Response({
            "subjects": subjects,
            "classrooms": classrooms,
            "teachers": teachers,
        })
    except Exception as e:
        print("Error in course_form_options:", e)
        traceback.print_exc()
        return Response({"detail": "An unexpected error occurred.", "error": str(e)},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR)




# ----------------------------
# ViewSet
# ----------------------------

@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
class SubjectViewSet(viewsets.ViewSet):
    """
    Endpoints:
      - GET    /api/subjects/           -> list (search with ?q=)
      - POST   /api/subjects/           -> create {name, code}
      - GET    /api/subjects/{id}/      -> detail (with counts)
      - PATCH  /api/subjects/{id}/      -> partial update
      - DELETE /api/subjects/{id}/      -> delete

    Auth:
      - API Key:     via HasAPIKey (e.g., header `X-API-Key: <key>`)
      - Session key: via SessionTokenAuthentication (e.g., header `Authorization: Session <sessionToken>`)
    """

    def _base_queryset(self, org):
        """
        Annotate per-subject counters within the resolved organization:
          - courses:  count of courses tied to the subject
          - teachers: distinct teacher profiles across those courses
          - students: distinct students across enrollments in those courses
        """
        return (
            Subject.objects
            .filter(organization=org)
            .annotate(
                courses=Count("course", filter=Q(course__organization=org), distinct=True),
                teachers=Count("course__teacher", filter=Q(course__organization=org), distinct=True),
                students=Count("course__enrollments__student", filter=Q(course__organization=org), distinct=True),
            )
            .order_by("name")
        )

    # -------- list --------
    def list(self, request, *args, **kwargs):
        org, org_error = _resolve_org(request)
        if org_error:
            return org_error

        q = (request.query_params.get("q") or "").strip()
        qs = self._base_queryset(org)
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(code__icontains=q))

        data = SubjectListItemSerializer(qs, many=True).data
        return Response(data)

    # -------- create --------
    def create(self, request, *args, **kwargs):
        org, org_error = _resolve_org(request)
        if org_error:
            return org_error

        serializer = SubjectWriteSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        # Enforce uniqueness within organization (name)
        name = serializer.validated_data["name"]
        if Subject.objects.filter(organization=org, name__iexact=name).exists():
            return Response({"detail": "Subject with this name already exists in the organization."},
                            status=status.HTTP_400_BAD_REQUEST)

        subject = Subject.objects.create(
            organization=org,
            name=serializer.validated_data["name"],
            code=serializer.validated_data.get("code", ""),
        )

        # Return as list-item shape (with counters defaulting to 0)
        subject_qs = self._base_queryset(org).filter(pk=subject.pk)
        data = SubjectListItemSerializer(subject_qs.first()).data
        return Response(data, status=status.HTTP_201_CREATED)

    # -------- retrieve --------
    def retrieve(self, request, pk=None, *args, **kwargs):
        org, org_error = _resolve_org(request)
        if org_error:
            return org_error

        subject = self._base_queryset(org).filter(pk=pk).first()
        if not subject:
            return Response({"detail": "Subject not found."}, status=status.HTTP_404_NOT_FOUND)

        return Response(SubjectListItemSerializer(subject).data)

    # -------- partial update --------
    def partial_update(self, request, pk=None, *args, **kwargs):
        org, org_error = _resolve_org(request)
        if org_error:
            return org_error

        subject = Subject.objects.filter(organization=org, pk=pk).first()
        if not subject:
            return Response({"detail": "Subject not found."}, status=status.HTTP_404_NOT_FOUND)

        serializer = SubjectWriteSerializer(subject, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        # If name is being changed, maintain org-level uniqueness
        new_name = serializer.validated_data.get("name")
        if new_name and Subject.objects.filter(organization=org, name__iexact=new_name).exclude(pk=pk).exists():
            return Response({"detail": "Another subject with this name already exists in the organization."},
                            status=status.HTTP_400_BAD_REQUEST)

        serializer.save()
        # Return the annotated version for the UI cards
        subject_qs = self._base_queryset(org).filter(pk=pk)
        return Response(SubjectListItemSerializer(subject_qs.first()).data)

    # -------- delete --------
    def destroy(self, request, pk=None, *args, **kwargs):
        org, org_error = _resolve_org(request)
        if org_error:
            return org_error

        subject = Subject.objects.filter(organization=org, pk=pk).first()
        if not subject:
            return Response({"detail": "Subject not found."}, status=status.HTTP_404_NOT_FOUND)

        has_courses = Course.objects.filter(organization=org, subject=subject).exists()
        if has_courses:
            return Response({"detail": "Cannot delete a subject that has courses."},
            status=status.HTTP_409_CONFLICT)

        subject.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)



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
            .filter(organization=org, course_type="public", classroom=OuterRef("pk"))
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

        qs = (
            StudentProfile.objects
            .select_related("user", "current_classroom")
            .filter(organization=org)
        )

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

        def avatar_url(u):
            f = getattr(u, "avatar", None)
            if not f:
                return None
            try:
                url = f.url
                if url.startswith("http"):
                    return url
                return request.build_absolute_uri(url)
            except Exception:
                return None

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
                "avatar": avatar_url(user),  # <-- added
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






# ---- util: get (or create) a TEACHER membership for a user within org ----
def _get_or_create_teacher_membership(user: User, org: Organization) -> OrganizationMembership:
    mem, _ = OrganizationMembership.objects.get_or_create(
        user=user, organization=org, role=OrganizationMembership.Role.TEACHER,
        defaults={"is_active": True}
    )
    return mem



# ---- VIEWSET ----

class TeacherViewSet(viewsets.ModelViewSet):
    """
    Endpoint: /api/teachers/
    Auth: API Key + Session Token
    """
    queryset = (
        TeacherProfile.objects.select_related("user", "organization")
        .annotate(courses_count=Count("courses", distinct=True))
        .all()
    )
    permission_classes = [HasAPIKey, IsAuthenticated]
    authentication_classes = [SessionTokenAuthentication]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get_serializer_class(self):
        if self.action in ["create", "update", "partial_update"]:
            return TeacherWriteSerializer
        if self.action in ["list"]:
            return TeacherListSerializer
        return TeacherDetailSerializer

    def _resolve(self, request):
        # Use your provided resolver
        org, error = _resolve_org(request)
        return org, error

    def get_queryset(self):
        org, _err = self._resolve(self.request)
        if not org:
            return TeacherProfile.objects.none()
        qs = super().get_queryset().filter(organization=org)

        # --- searching and light filtering (matches your UI) ---
        q = self.request.query_params.get("q")
        if q:
            qs = qs.filter(
                Q(user__email__icontains=q) |
                Q(user__first_name__icontains=q) |
                Q(user__last_name__icontains=q) |
                Q(bio__icontains=q) |
                Q(specialties__name__icontains=q)
            ).distinct()
        # optional status filter ?status=active|inactive|suspended
        status_param = self.request.query_params.get("status")
        if status_param in {"active", "inactive", "suspended"}:
            # filter in python due to derived nature
            ids = []
            for t in qs:
                mem = OrganizationMembership.objects.filter(
                    user=t.user, organization=org, role=OrganizationMembership.Role.TEACHER
                ).first()
                if _status_from_user_membership(t.user, mem) == status_param:
                    ids.append(t.id)
            qs = qs.filter(id__in=ids)
        return qs

    # ----- LIST -----
    def list(self, request, *args, **kwargs):
        org, error = self._resolve(request)
        if error:
            return error
        return super().list(request, *args, **kwargs)

    # ----- RETRIEVE -----
    def retrieve(self, request, *args, **kwargs):
        org, error = self._resolve(request)
        if error:
            return error
        return super().retrieve(request, *args, **kwargs)

    # ----- CREATE -----
    @transaction.atomic
    def create(self, request, *args, **kwargs):
        org, error = self._resolve(request)
        if error:
            return error
        ser = self.get_serializer(data=request.data, context={"org": org})
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        email = data["email"].lower().strip()
        name = data.get("name") or ""
        first, *rest = name.split(" ", 1)
        last = rest[0] if rest else ""
        # Get or create the user by email
        user, _ = User.objects.get_or_create(
            email=email,
            primary_org=org,
            defaults={"first_name": first, "last_name": last, "is_active": True}
        )
        # Optional user fields
        if "phone" in data:
            user.phone = data["phone"] or user.phone

        # Optional avatar upload, even if UI didn’t include it
        avatar = request.FILES.get("avatar")
        if avatar:
            user.avatar = avatar
        user.save()
        # Ensure teacher membership
        membership = _get_or_create_teacher_membership(user, org)

        # Apply status chip if provided
        if "status" in data:
            _apply_status_to_user_membership(data["status"], user, membership)
            user.save(update_fields=["is_active"])
            membership.save(update_fields=["is_active"])

        # Create TeacherProfile (or attach if exists but in another org this would be separate rows)
        tp, created = TeacherProfile.objects.get_or_create(
            user=user, organization=org,
            defaults={
                "bio": data.get("bio", "") or "",
                "experience": data.get("experience", 0) or 0,
            }
        )
        if not created:
            tp.bio = data.get("bio", tp.bio)
            tp.experience = data.get("experience", tp.experience)
            tp.save()

        # Specialties (validated to exist)
        if "specialties" in data:
            tp.specialties.set(Subject.objects.filter(organization=org, name__in=data["specialties"]))

        # Respond with detail serializer including avatarUrl
        out = TeacherDetailSerializer(
            TeacherProfile.objects.annotate(courses_count=Count("courses", distinct=True)).get(pk=tp.pk)
        )
        headers = self.get_success_headers(out.data)
        return Response(out.data, status=status.HTTP_201_CREATED, headers=headers)

    # ----- UPDATE / PARTIAL_UPDATE -----
    @transaction.atomic
    def partial_update(self, request, *args, **kwargs):
        org, error = self._resolve(request)
        if error:
            return error

        instance: TeacherProfile = self.get_object()
        ser = self.get_serializer(data=request.data, partial=True, context={"org": org})
        ser.is_valid(raise_exception=True)
        data = ser.validated_data

        user = instance.user

        # Update user basics if present
        if "email" in data:
            user.email = data["email"].lower().strip()
        if "name" in data:
            first, *rest = data["name"].split(" ", 1)
            user.first_name = first
            user.last_name = rest[0] if rest else user.last_name
        if "phone" in data:
            user.phone = data["phone"]

        # Admin can update avatar from this endpoint (multipart)
        avatar = request.FILES.get("avatar")
        if avatar:
            user.avatar = avatar
        user.save()

        # Update profile
        if "bio" in data:
            instance.bio = data["bio"]
        if "experience" in data:
            instance.experience = data["experience"]
        if "specialties" in data:
            instance.specialties.set(Subject.objects.filter(organization=org, name__in=data["specialties"]))
        instance.save()

        # Apply status if provided
        if "status" in data:
            mem = _get_or_create_teacher_membership(user, org)
            _apply_status_to_user_membership(data["status"], user, mem)
            user.save(update_fields=["is_active"])
            mem.save(update_fields=["is_active"])

        # Out
        out = TeacherDetailSerializer(
            TeacherProfile.objects.annotate(courses_count=Count("courses", distinct=True)).get(pk=instance.pk)
        )
        return Response(out.data)

    # ----- DESTROY (guard against assigned courses) -----
    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        org, error = self._resolve(request)
        if error:
            return error

        instance: TeacherProfile = self.get_object()
        has_courses = Course.objects.filter(teacher=instance).exists()
        if has_courses:
            return Response(
                {"detail": "Teacher cannot be deleted because they are assigned to one or more courses."},
                status=status.HTTP_409_CONFLICT,
            )

        # Safe to delete TeacherProfile; do NOT delete the User (could be member in other orgs/roles)
        user = instance.user
        instance.delete()
        user.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    # ----- ACTION: explicit avatar upload (alternative to PATCH with multipart) -----
    @action(detail=True, methods=["POST"], url_path="avatar", parser_classes=[MultiPartParser, FormParser])
    def upload_avatar(self, request, pk=None):
        org, error = self._resolve(request)
        if error:
            return error
        instance: TeacherProfile = self.get_object()
        file = request.FILES.get("avatar")
        if not file:
            return Response({"detail": "avatar file is required."}, status=status.HTTP_400_BAD_REQUEST)
        instance.user.avatar = file
        instance.user.save(update_fields=["avatar"])
        return Response({"avatarUrl": instance.user.avatar.url})




class ParentViewSet(viewsets.ModelViewSet):
    """
    CRUD for ParentProfile within the caller's organization.
    Auth: API Key + Session Token (same as your sample).
    - list/retrieve return avatar_url
    - admin may update User.avatar via multipart
    - delete blocked if parent has children links
    - search via ?q=
    Extra actions:
      - POST /parents/{id}/link-child/ {student_id}
      - POST /parents/{id}/unlink-child/ {student_id}
      - POST /parents/{id}/set-status/ {"status": "active|inactive|suspended"}
      - POST /parents/{id}/generate-invoices/
    """

    authentication_classes = [SessionTokenAuthentication]
    permission_classes = [HasAPIKey, IsAuthenticated]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    # ❗ DRF requires either serializer_class or get_serializer_class()
    serializer_class = ParentWriteSerializer

    # ------------- utils -------------

    def _print_exception(self, e):
        print("\n[ERROR] Exception in ParentViewSet:")
        print(f"Type: {type(e).__name__}")
        print(f"Message: {e}")
        traceback.print_exc()

    def _ensure_org_or_respond(self):
        """
        Ensure self._org is set (from _resolve_org). If _resolve_org returned a Response,
        return it so the caller can short-circuit.
        """
        try:
            if getattr(self, "_org", None) is None:
                org, error = _resolve_org(self.request)
                if error:
                    return None, error
                self._org = org
            return self._org, None
        except Exception as e:
            self._print_exception(e)
            return None, Response({"detail": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    # ------------- queryset / serializers -------------

    def get_queryset(self):
        try:
            org, error = _resolve_org(self.request)
            self._org = org
            if error:
                # list/retrieve will short-circuit and return the error Response
                return ParentProfile.objects.none()

            q = (
                ParentProfile.objects
                .filter(organization=org)
                .select_related("user", "organization_subscription")
                .annotate(children_count=Count("children_links", distinct=True))
            )

            term = (self.request.query_params.get("q") or "").strip()
            if term:
                q = q.filter(
                    Q(user__email__icontains=term)
                    | Q(user__first_name__icontains=term)
                    | Q(user__last_name__icontains=term)
                    | Q(address__icontains=term)
                    | Q(user__phone__icontains=term)
                )

            return q.order_by("-created_at")
        except Exception as e:
            self._print_exception(e)
            return ParentProfile.objects.none()

    def get_serializer_class(self):
        # Choose serializers by action
        if self.action in ["list"]:
            return ParentListSerializer
        if self.action in ["retrieve"]:
            return ParentDetailSerializer
        # default for create/update
        return ParentWriteSerializer

    def get_serializer_context(self):
        # Provide org + creating to write serializer
        ctx = super().get_serializer_context()
        try:
            ctx["org"] = getattr(self, "_org", None)
            if self.action in ["create"]:
                ctx["creating"] = True
        except Exception as e:
            self._print_exception(e)
        return ctx

    # ------------- CRUD -------------

    def list(self, request, *args, **kwargs):
        try:
            org, error = self._ensure_org_or_respond()
            if error:
                return error
            return super().list(request, *args, **kwargs)
        except Exception as e:
            self._print_exception(e)
            return Response({"detail": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def retrieve(self, request, *args, **kwargs):
        try:
            org, error = self._ensure_org_or_respond()
            if error:
                return error
            return super().retrieve(request, *args, **kwargs)
        except Exception as e:
            self._print_exception(e)
            return Response({"detail": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def create(self, request, *args, **kwargs):
        try:
            org, error = self._ensure_org_or_respond()
            if error:
                return error

            serializer = self.get_serializer(data=request.data)
            serializer.is_valid(raise_exception=True)

            # Save -> ParentProfile
            parent: ParentProfile = serializer.save()

            # ❗ set primary_org on the related User
            user = parent.user
            if user.primary_org_id != getattr(org, "id", None):
                user.primary_org = org
                user.save(update_fields=["primary_org"])

                parent.organization_subscription = OrganizationSubscription.get_lastest_org_sub(org)
                parent.save()

            # Return detail payload
            detail = ParentDetailSerializer(parent, context={"request": request}).data
            return Response(detail, status=status.HTTP_201_CREATED)

        except Exception as e:
            self._print_exception(e)
            return Response({"detail": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def update(self, request, *args, **kwargs):
        try:
            org, error = self._ensure_org_or_respond()
            if error:
                return error
            partial = kwargs.pop("partial", False)
            instance = self.get_object()
            serializer = self.get_serializer(instance, data=request.data, partial=partial)
            serializer.is_valid(raise_exception=True)
            instance = serializer.save()
            data = ParentDetailSerializer(instance, context={"request": request}).data
            return Response(data, status=status.HTTP_200_OK)
        except Exception as e:
            self._print_exception(e)
            return Response({"detail": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def destroy(self, request, *args, **kwargs):
        try:
            org, error = self._ensure_org_or_respond()
            if error:
                return error
            instance: ParentProfile = self.get_object()
            if instance.children_links.exists():
                return Response(
                    {"detail": "Cannot delete parent while children are linked."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            with transaction.atomic():
                parent = instance.user
                instance.delete()
                parent.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        except Exception as e:
            self._print_exception(e)
            return Response({"detail": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    # ------------- Extra actions -------------

    @action(detail=True, methods=["post"])
    def link_child(self, request, pk=None):
        try:
            org, error = self._ensure_org_or_respond()
            if error:
                return error
            parent = self.get_object()
            email = request.data.get("student_email")
            if not email:
                return Response({"detail": "email is required."}, status=status.HTTP_400_BAD_REQUEST)
            student = StudentProfile.objects.filter(user__email=email, organization=org).first()
            if not student:
                return Response({"detail": "Student not found in this organization."}, status=status.HTTP_404_NOT_FOUND)
            ParentChildLink.objects.get_or_create(parent=parent, student=student)
            data = ParentDetailSerializer(parent, context={"request": request}).data
            return Response(data, status=status.HTTP_200_OK)
        except Exception as e:
            self._print_exception(e)
            return Response({"detail": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=["post"])
    def unlink_child(self, request, pk=None):
        try:
            org, error = self._ensure_org_or_respond()
            if error:
                return error
            parent = self.get_object()
            student_id = request.data.get("student_id")
            if not student_id:
                return Response({"detail": "student_id is required."}, status=status.HTTP_400_BAD_REQUEST)
            ParentChildLink.objects.filter(parent=parent, student_id=student_id).delete()
            data = ParentDetailSerializer(parent, context={"request": request}).data
            return Response(data, status=status.HTTP_200_OK)
        except Exception as e:
            self._print_exception(e)
            return Response({"detail": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=["post"])
    def set_status(self, request, pk=None):
        try:
            org, error = self._ensure_org_or_respond()
            if error:
                return error
            parent = self.get_object()
            status_value = request.data.get("status")
            if status_value not in ("active", "inactive", "suspended"):
                return Response({"detail": "Invalid status."}, status=status.HTTP_400_BAD_REQUEST)

            membership = _get_or_create_parent_membership(parent.user, org)
            _apply_status_to_user_membership(status_value, parent.user, membership)
            parent.user.save(update_fields=["is_active"])
            membership.save(update_fields=["is_active"])

            data = ParentDetailSerializer(parent, context={"request": request}).data
            return Response(data, status=status.HTTP_200_OK)
        except Exception as e:
            self._print_exception(e)
            return Response({"detail": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=["post"])
    def generate_invoices(self, request, pk=None):
        try:
            org, error = self._ensure_org_or_respond()
            if error:
                return error
            parent: ParentProfile = self.get_object()
            created = parent.generate_subscription_invoices()
            return Response(
                {"created": len(created), "invoice_ids": [inv.id for inv in created]},
                status=status.HTTP_200_OK,
            )
        except Exception as e:
            self._print_exception(e)
            return Response({"detail": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)







@api_view(["GET", "POST"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def admin_student_enrollments(request, student_id: int):
    """
    GET: List all enrollments for student (org scoped) + optional search.
    POST: Assign a new enrollment to the student. Body: { "course_id": <int> }
    """
    try:
        org, err = _resolve_org(request)
        if err:
            return err

        if not _is_org_admin_or_teacher(request, org):
            return Response({"detail": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

        student = (StudentProfile.objects
                   .select_related("user", "organization")
                   .filter(id=student_id, organization=org)
                   .first())
        if not student:
            return Response({"detail": "Student not found in this organization."},
                            status=status.HTTP_404_NOT_FOUND)

        if request.method == "GET":
            q = (request.query_params.get("q") or "").strip()

            qs = (Enrollment.objects
                  .filter(student=student)
                  .select_related("course__subject", "course__classroom", "course__teacher__user")
                  .order_by("-id"))

            if q:
                qs = qs.filter(
                    Q(course__name__icontains=q) |
                    Q(course__subject__name__icontains=q) |
                    Q(course__classroom__name__icontains=q) |
                    Q(course__teacher__user__first_name__icontains=q) |
                    Q(course__teacher__user__last_name__icontains=q) |
                    Q(course__teacher__user__email__icontains=q)
                )

            return Response([_enrollment_to_dict(e) for e in qs], status=status.HTTP_200_OK)

        # POST -> create enrollment
        data = request.data or {}
        course_id, err = _parse_positive_int(data.get("course_id"), "course_id")
        if err:
            return err
        if not course_id:
            return Response({"detail": "course_id is required."}, status=status.HTTP_400_BAD_REQUEST)

        course = (Course.objects
                  .select_related("subject", "classroom", "teacher__user")
                  .filter(id=course_id, organization=org)
                  .first())
        if not course:
            return Response({"detail": "Course not found in this organization."},
                            status=status.HTTP_404_NOT_FOUND)

        exists = Enrollment.objects.filter(student=student, course=course).exists()
        if exists:
            return Response({"detail": "Student is already enrolled in this course."},
                            status=status.HTTP_400_BAD_REQUEST)

        leaderboard_season = resolve_season(org, timezone.now())
        
        e = Enrollment.objects.create(student=student, course=course, leaderboard_season=leaderboard_season)
        e = (Enrollment.objects
             .select_related("course__subject", "course__classroom", "course__teacher__user")
             .get(id=e.id))

        return Response(_enrollment_to_dict(e), status=status.HTTP_201_CREATED)

    except Exception as e:
        print("Error in admin_student_enrollments:", e)
        traceback.print_exc()
        return Response({"detail": "An unexpected error occurred.", "error": str(e)},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def admin_available_courses_for_student(request, student_id: int):
    """
    GET: List active courses in org that student is NOT yet enrolled in.
    Optional: ?q=search
    """
    try:
        org, err = _resolve_org(request)
        if err:
            return err

        if not _is_org_admin_or_teacher(request, org):
            return Response({"detail": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

        student = StudentProfile.objects.filter(id=student_id, organization=org).first()
        if not student:
            return Response({"detail": "Student not found in this organization."},
                            status=status.HTTP_404_NOT_FOUND)

        q = (request.query_params.get("q") or "").strip()
        enrolled_ids = Enrollment.objects.filter(student=student).values_list("course_id", flat=True)

        qs = (Course.objects
              .filter(organization=org, is_active=True, course_type='public')
              .exclude(id__in=enrolled_ids)
              .select_related("subject", "classroom", "teacher__user")
              .order_by("name"))

        if q:
            qs = qs.filter(
                Q(name__icontains=q) |
                Q(subject__name__icontains=q) |
                Q(classroom__name__icontains=q) |
                Q(teacher__user__first_name__icontains=q) |
                Q(teacher__user__last_name__icontains=q) |
                Q(teacher__user__email__icontains=q)
            )

        # Reuse your course card shape (optional). Here’s a minimal option:
        data = []
        for c in qs:
            teacher = c.teacher.user.get_full_name() or c.teacher.user.email or ""
            data.append({
                "id": c.id,
                "name": c.name,
                "subject": c.subject.name,
                "classroom": c.classroom.name,
                "teacher": teacher,
                "course_type": c.course_type,
            })

        return Response(data, status=status.HTTP_200_OK)

    except Exception as e:
        print("Error in admin_available_courses_for_student:", e)
        traceback.print_exc()
        return Response({"detail": "An unexpected error occurred.", "error": str(e)},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR)
