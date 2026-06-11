"""
Teacher Report Views
--------------------
CRUD for teacher reports, student/parent viewing, public access, and parent onboarding.
"""
import json
import traceback
from decimal import Decimal
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.contrib.auth.hashers import check_password

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.response import Response
from rest_framework_api_key.permissions import HasAPIKey

from api.authentication import SessionTokenAuthentication
from orgs.models import Organization, OrganizationMembership
from accounts.models import User
from academics.models import (
    StudentProfile, TeacherProfile, ParentProfile, ParentChildLink,
    TeacherReport, ReportCBTItem, ReportCodingItem, ReportActivity,
    ReportVideo, ReportRecipient,
)
from learning.models import Course, Enrollment, Lesson
from assessments.models import Test, TestAttempt
from codeide.models import CodeProject
from core.utils import _resolve_org, _is_org_admin_or_teacher


# ─── Helpers ────────────────────────────────────────────────

def _serialize_report_list(report):
    return {
        "id": report.id,
        "title": report.title,
        "status": report.status,
        "recipient_mode": report.recipient_mode,
        "course_id": report.course_id,
        "course_name": report.course.name if report.course else "",
        "organization_name": report.organization.name if report.organization else "",
        "organization_logo": (
            report.organization.logo.url
            if report.organization and report.organization.logo
            else None
        ),
        "recipients_count": report.recipients.count(),
        "published_at": report.published_at,
        "period_start": report.period_start,
        "period_end": report.period_end,
        "share_token": report.share_token,
        "created_at": report.created_at,
    }



def _resolve_video_url(v, request=None):
    """
    Return a fully-qualified, playable URL for a ReportVideo object.

    Priority:
      1. video_file (Django FileField) — build absolute URL via request or settings BASE_URL
      2. video_url  — if it already starts with http, return as-is;
                      otherwise treat it as a storage key and build /media/<key> absolute URL
    """
    base = ""
    if request:
        base = request.build_absolute_uri("/").rstrip("/")
    else:
        base = getattr(settings, "BASE_URL", "").rstrip("/")

    # Prefer the uploaded file
    if v.video_file:
        try:
            rel = v.video_file.url  # e.g. /media/texagon/reports/1/videos/.../file.mp4
            if rel.startswith("http"):
                return rel
            return f"{base}{rel}"
        except ValueError:
            pass

    raw = (v.video_url or "").strip()
    if not raw:
        return ""

    # Already a full URL (YouTube, S3 signed URL, etc.)
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw

    # Local storage key — build absolute media URL
    # e.g. key = "lessons/Creating_Accounts.mp4" → /media/lessons/Creating_Accounts.mp4
    return f"{base}/media/{raw.lstrip('/')}"


def _serialize_report_detail(report, student=None, request=None):
    """Full report serialization. If student is given, include per-student scores."""
    cbt_items = []
    for item in report.cbt_items.select_related("test").all():
        cbt_items.append({
            "id": item.id,
            "test_id": item.test_id,
            "test_title": item.test_title or (item.test.title if item.test else ""),
            "total_marks": str(item.total_marks),
        })

    coding_items = []
    for item in report.coding_items.select_related("lesson").all():
        coding_items.append({
            "id": item.id,
            "lesson_id": item.lesson_id,
            "lesson_title": item.lesson_title or (item.lesson.name if item.lesson else ""),
        })

    activities = []
    for act in report.activities.all():
        activities.append({
            "id": act.id,
            "title": act.title,
            "description": act.description,
            "activity_date": act.activity_date,
            "order": act.order,
        })

    videos = []
    for v in report.videos.all():
        resolved_url = _resolve_video_url(v, request)
        videos.append({
            "id": v.id,
            "title": v.title,
            # Always return the resolved, absolute playable URL as video_url
            "video_url": resolved_url,
            # Keep the raw file URL separately for debugging
            "video_file": v.video_file.url if v.video_file else None,
        })

    data = {
        "id": report.id,
        "title": report.title,
        "description": report.description,
        "status": report.status,
        "recipient_mode": report.recipient_mode,
        "course": {
            "id": report.course_id,
            "name": report.course.name if report.course else "",
        },
        "organization": {
            "id": report.organization_id,
            "name": report.organization.name if report.organization else "",
            "logo": report.organization.logo.url if report.organization and report.organization.logo else None,
        },
        "teacher": {
            "name": report.teacher.user.get_full_name() if report.teacher else "",
            "email": report.teacher.user.email if report.teacher else "",
        },
        "period_start": report.period_start,
        "period_end": report.period_end,
        "published_at": report.published_at,
        "share_token": report.share_token,
        "cbt_items": cbt_items,
        "coding_items": coding_items,
        "activities": activities,
        "videos": videos,
        "created_at": report.created_at,
    }

    # If viewing for a specific student, include their recipient data
    if student:
        recipient = report.recipients.filter(student=student).first()
        if recipient:
            data["student_data"] = {
                "student_id": student.id,
                "student_name": (student.user.get_full_name() or student.user.email).strip(),
                "admission_no": student.admission_no or "",
                "classroom": student.current_classroom.name if student.current_classroom else "",
                "cbt_scores": recipient.cbt_scores,
                "coding_scores": recipient.coding_scores,
                "teacher_remark": recipient.teacher_remark,
            }

    return data


def _snapshot_student_scores(report, student):
    """Build score snapshots for a student from live data."""
    cbt_scores = {}
    for item in report.cbt_items.all():
        attempt = (
            TestAttempt.objects
            .filter(student=student, test_id=item.test_id, status__in=["submitted", "graded"])
            .order_by("-score")
            .first()
        )
        cbt_scores[str(item.test_id)] = {
            "score": str(attempt.score) if attempt else "0",
            "total": str(item.total_marks),
            "status": attempt.status if attempt else "not_attempted",
        }

    coding_scores = {}
    for item in report.coding_items.all():
        project = (
            CodeProject.objects
            .filter(student=student, lesson_id=item.lesson_id)
            .order_by("-created_at")
            .first()
        )
        coding_scores[str(item.lesson_id)] = {
            "score": str(project.score) if project and project.score else "0",
            "feedback": project.feedback if project else "",
            "project_title": project.title if project else "",
            "status": project.status if project else "not_submitted",
        }

    return cbt_scores, coding_scores


# ─── Teacher Endpoints ──────────────────────────────────────

@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def teacher_report_list(request):
    """List all reports created by the current teacher."""
    org, err = _resolve_org(request)
    if err:
        return err

    tp = TeacherProfile.objects.filter(user=request.user, organization=org).first()
    if not tp:
        return Response({"detail": "Teacher profile not found."}, status=status.HTTP_403_FORBIDDEN)

    reports = (
        TeacherReport.objects
        .filter(teacher=tp, organization=org)
        .select_related("course")
        .order_by("-created_at")
    )
    return Response({"results": [_serialize_report_list(r) for r in reports]})


@api_view(["POST"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def teacher_report_create(request):
    """Create a new report (draft)."""
    org, err = _resolve_org(request)
    if err:
        return err

    tp = TeacherProfile.objects.filter(user=request.user, organization=org).first()
    if not tp:
        return Response({"detail": "Teacher profile not found."}, status=status.HTTP_403_FORBIDDEN)

    data = request.data
    course_id = data.get("course_id")
    if not course_id:
        return Response({"detail": "course_id is required."}, status=status.HTTP_400_BAD_REQUEST)

    course = Course.objects.filter(id=course_id, organization=org, teacher=tp).first()
    if not course:
        return Response({"detail": "Course not found or not yours."}, status=status.HTTP_404_NOT_FOUND)

    with transaction.atomic():
        report = TeacherReport.objects.create(
            organization=org,
            teacher=tp,
            course=course,
            title=data.get("title", "Activity Report"),
            description=data.get("description", ""),
            recipient_mode=data.get("recipient_mode", "selected"),
            period_start=data.get("period_start") or None,
            period_end=data.get("period_end") or None,
        )

        # CBT items
        for test_id in data.get("cbt_test_ids", []):
            test = Test.objects.filter(id=test_id, course=course).first()
            if test:
                ReportCBTItem.objects.create(report=report, test=test)

        # Coding items
        for lesson_id in data.get("coding_lesson_ids", []):
            lesson = Lesson.objects.filter(id=lesson_id, module__course=course).first()
            if lesson:
                ReportCodingItem.objects.create(report=report, lesson=lesson)

        # Activities
        for i, act in enumerate(data.get("activities", [])):
            ReportActivity.objects.create(
                report=report,
                title=act.get("title", ""),
                description=act.get("description", ""),
                activity_date=act.get("activity_date") or None,
                order=i,
            )

        # Video URLs
        for vid in data.get("videos", []):
            ReportVideo.objects.create(
                report=report,
                title=vid.get("title", ""),
                video_url=vid.get("video_url", ""),
            )

        # Recipients
        student_ids = data.get("student_ids", [])
        if data.get("recipient_mode") == "course":
            student_ids = list(
                Enrollment.objects.filter(course=course, status="active")
                .values_list("student_id", flat=True)
            )
        elif data.get("recipient_mode") == "classroom":
            classroom_id = course.classroom_id
            student_ids = list(
                StudentProfile.objects.filter(
                    organization=org, current_classroom_id=classroom_id
                ).values_list("id", flat=True)
            )

        for sid in student_ids:
            ReportRecipient.objects.get_or_create(report=report, student_id=sid)

    return Response(
        {"detail": "Report created.", "report": _serialize_report_list(report)},
        status=status.HTTP_201_CREATED,
    )


@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def teacher_report_detail(request, report_id):
    """Get full report detail (teacher view)."""
    org, err = _resolve_org(request)
    if err:
        return err

    tp = TeacherProfile.objects.filter(user=request.user, organization=org).first()
    if not tp:
        return Response({"detail": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

    report = (
        TeacherReport.objects
        .filter(id=report_id, teacher=tp, organization=org)
        .select_related("course", "organization", "teacher__user")
        .first()
    )
    if not report:
        return Response({"detail": "Report not found."}, status=status.HTTP_404_NOT_FOUND)

    data = _serialize_report_detail(report, request=request)

    # Include recipients list for teacher
    recipients = []
    for r in report.recipients.select_related("student__user", "student__current_classroom").all():
        recipients.append({
            "id": r.id,
            "student_id": r.student_id,
            "student_name": (r.student.user.get_full_name() or r.student.user.email).strip(),
            "admission_no": r.student.admission_no or "",
            "classroom": r.student.current_classroom.name if r.student.current_classroom else "",
            "cbt_scores": r.cbt_scores,
            "coding_scores": r.coding_scores,
            "teacher_remark": r.teacher_remark,
            "parent_viewed": r.parent_viewed,
        })
    data["recipients"] = recipients

    return Response(data)


@api_view(["PATCH"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def teacher_report_update(request, report_id):
    """Update a draft report."""
    org, err = _resolve_org(request)
    if err:
        return err

    tp = TeacherProfile.objects.filter(user=request.user, organization=org).first()
    if not tp:
        return Response({"detail": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

    report = TeacherReport.objects.filter(id=report_id, teacher=tp, organization=org, status="draft").first()
    if not report:
        return Response({"detail": "Draft report not found."}, status=status.HTTP_404_NOT_FOUND)

    data = request.data
    for field in ["title", "description", "recipient_mode", "period_start", "period_end"]:
        if field in data:
            setattr(report, field, data[field] or (None if field in ["period_start", "period_end"] else ""))
    report.save()

    return Response({"detail": "Updated.", "report": _serialize_report_list(report)})


@api_view(["POST"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def teacher_report_publish(request, report_id):
    """Publish a report - snapshots scores and marks it published."""
    org, err = _resolve_org(request)
    if err:
        return err

    tp = TeacherProfile.objects.filter(user=request.user, organization=org).first()
    if not tp:
        return Response({"detail": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

    report = TeacherReport.objects.filter(id=report_id, teacher=tp, organization=org, status="draft").first()
    if not report:
        return Response({"detail": "Draft report not found."}, status=status.HTTP_404_NOT_FOUND)

    with transaction.atomic():
        # Snapshot scores for each recipient
        for recipient in report.recipients.select_related("student").all():
            cbt_scores, coding_scores = _snapshot_student_scores(report, recipient.student)
            recipient.cbt_scores = cbt_scores
            recipient.coding_scores = coding_scores
            recipient.save(update_fields=["cbt_scores", "coding_scores"])

        report.status = "published"
        report.published_at = timezone.now()
        report.save(update_fields=["status", "published_at"])

    return Response({"detail": "Report published.", "share_token": report.share_token})


@api_view(["DELETE"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def teacher_report_delete(request, report_id):
    """Delete a draft report."""
    org, err = _resolve_org(request)
    if err:
        return err

    tp = TeacherProfile.objects.filter(user=request.user, organization=org).first()
    if not tp:
        return Response({"detail": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

    report = TeacherReport.objects.filter(id=report_id, teacher=tp, organization=org, status="draft").first()
    if not report:
        return Response({"detail": "Draft report not found."}, status=status.HTTP_404_NOT_FOUND)

    report.delete()
    return Response({"detail": "Deleted."})


@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def teacher_report_student_data(request):
    """Get available CBT tests and coding lessons for a course (for report creation)."""
    org, err = _resolve_org(request)
    if err:
        return err

    course_id = request.query_params.get("course_id")
    if not course_id:
        return Response({"detail": "course_id required."}, status=status.HTTP_400_BAD_REQUEST)

    tp = TeacherProfile.objects.filter(user=request.user, organization=org).first()
    if not tp:
        return Response({"detail": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

    course = Course.objects.filter(id=course_id, organization=org, teacher=tp).first()
    if not course:
        return Response({"detail": "Course not found."}, status=status.HTTP_404_NOT_FOUND)

    tests = Test.objects.filter(course=course).order_by("-created_at")
    test_data = [{"id": t.id, "title": t.title, "total_marks": str(t.total_marks), "visibility": t.visibility} for t in tests]

    lessons = Lesson.objects.filter(module__course=course).select_related("module").order_by("module__order", "order")
    lesson_data = [{"id": l.id, "name": l.name, "module_name": l.module.name} for l in lessons]

    # Students enrolled (Active status only)
    enrollments = Enrollment.objects.filter(
        course=course,
        status=Enrollment.Status.ACTIVE
    ).select_related("student__user", "student__current_classroom")
    students = []
    for e in enrollments:
        s = e.student
        students.append({
            "id": s.id,
            "name": (s.user.get_full_name() or s.user.email).strip(),
            "admission_no": s.admission_no or "",
            "classroom": s.current_classroom.name if s.current_classroom else "",
        })

    return Response({"tests": test_data, "lessons": lesson_data, "students": students})


# ─── Student/Parent Report Viewing ──────────────────────────

@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def my_reports_list(request):
    """List reports for current student or parent's children."""
    user = request.user

    # Check if student
    sp = StudentProfile.objects.filter(user=user).first()
    if sp:
        recipient_ids = ReportRecipient.objects.filter(
            student=sp, report__status="published"
        ).values_list("report_id", flat=True)
        reports = TeacherReport.objects.filter(id__in=recipient_ids).select_related("course", "organization").order_by("-published_at")
        return Response({"results": [_serialize_report_list(r) for r in reports]})

    # Check if parent
    pp = ParentProfile.objects.filter(user=user).first()
    if pp:
        child_links = ParentChildLink.objects.filter(parent=pp).select_related(
            "student__user", "student__current_classroom"
        )
        child_ids = [link.student_id for link in child_links]

        # Build a lookup map: student_id → child info dict
        child_map = {}
        for link in child_links:
            s = link.student
            child_map[s.id] = {
                "id": s.id,
                "name": (s.user.get_full_name() or s.user.email).strip(),
                "admission_no": s.admission_no or "",
                "classroom": s.current_classroom.name if s.current_classroom else "",
            }

        recipient_ids = ReportRecipient.objects.filter(
            student_id__in=child_ids, report__status="published"
        ).values_list("report_id", flat=True)
        reports = TeacherReport.objects.filter(id__in=recipient_ids).select_related(
            "course", "organization"
        ).order_by("-published_at")

        results = []
        for r in reports:
            d = _serialize_report_list(r)
            # Include rich child objects for each child who is a recipient
            recipient_student_ids = list(
                ReportRecipient.objects.filter(report=r, student_id__in=child_ids)
                .values_list("student_id", flat=True)
            )
            d["children"] = [child_map[sid] for sid in recipient_student_ids if sid in child_map]
            results.append(d)
        return Response({"results": results})

    return Response({"results": []})


@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def my_report_detail(request, report_id):
    """View a single report for student or parent."""
    user = request.user
    student_id = request.query_params.get("student_id")

    sp = StudentProfile.objects.filter(user=user).first()
    pp = ParentProfile.objects.filter(user=user).first()

    target_student = None

    if sp:
        target_student = sp
    elif pp and student_id:
        # Parent viewing child's report
        link = ParentChildLink.objects.filter(parent=pp, student_id=student_id).first()
        if link:
            target_student = link.student

    if not target_student:
        return Response({"detail": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

    report = TeacherReport.objects.filter(
        id=report_id, status="published"
    ).select_related("course", "organization", "teacher__user").first()
    if not report:
        return Response({"detail": "Report not found."}, status=status.HTTP_404_NOT_FOUND)

    # Verify student is a recipient
    if not report.recipients.filter(student=target_student).exists():
        return Response({"detail": "Not a recipient."}, status=status.HTTP_403_FORBIDDEN)

    # Mark as viewed by parent
    if pp:
        ReportRecipient.objects.filter(report=report, student=target_student).update(
            parent_viewed=True, parent_viewed_at=timezone.now()
        )

    return Response(_serialize_report_detail(report, student=target_student, request=request))


# ─── Public Report Access + Parent Onboarding ───────────────

@api_view(["GET"])
@permission_classes([HasAPIKey])
def public_report_info(request, token):
    """Get basic report info for the public share page (no auth required)."""
    report = TeacherReport.objects.filter(
        share_token=token, status="published"
    ).select_related("organization", "course").first()
    if not report:
        return Response({"detail": "Report not found."}, status=status.HTTP_404_NOT_FOUND)

    # Optional session token authentication check
    user = None
    token_header = request.META.get("HTTP_X_SESSION_TOKEN")
    if not token_header:
        auth = request.META.get("HTTP_AUTHORIZATION", "")
        if auth.startswith("X-Session-Token "):
            token_header = auth[len("X-Session-Token ") :].strip()
        elif auth.startswith("Bearer "):
            token_header = auth[len("Bearer ") :].strip()

    if token_header:
        from api.models import SessionToken
        try:
            st = SessionToken.objects.select_related("user").get(key=token_header, is_active=True)
            if st.expires_at > timezone.now():
                user = st.user
        except SessionToken.DoesNotExist:
            pass

    if user:
        # Check student
        sp = StudentProfile.objects.filter(user=user).first()
        if sp:
            if report.recipients.filter(student=sp).exists():
                return Response({
                    "authenticated": True,
                    "report": _serialize_report_detail(report, student=sp, request=request)
                })

        # Check parent
        pp = ParentProfile.objects.filter(user=user).first()
        if pp:
            child_ids = ParentChildLink.objects.filter(parent=pp).values_list("student_id", flat=True)
            recipient = report.recipients.filter(student_id__in=child_ids).first()
            if recipient:
                # Mark as viewed by parent
                report.recipients.filter(student=recipient.student).update(
                    parent_viewed=True, parent_viewed_at=timezone.now()
                )
                return Response({
                    "authenticated": True,
                    "report": _serialize_report_detail(report, student=recipient.student, request=request)
                })

    return Response({
        "report_title": report.title,
        "organization_name": report.organization.name,
        "organization_logo": report.organization.logo.url if report.organization.logo else None,
        "course_name": report.course.name,
        "published_at": report.published_at,
    })


@api_view(["POST"])
@permission_classes([HasAPIKey])
def public_report_verify_student(request, token):
    """Verify student credentials for public report access."""
    report = TeacherReport.objects.filter(
        share_token=token, status="published"
    ).select_related("organization").first()
    if not report:
        return Response({"detail": "Report not found."}, status=status.HTTP_404_NOT_FOUND)

    identifier = (request.data.get("identifier") or "").strip().lower()
    password = request.data.get("password", "")

    if not identifier or not password:
        return Response({"detail": "Admission number/email and password required."}, status=status.HTTP_400_BAD_REQUEST)

    # Find student by admission_no or email
    student = None
    user = None

    sp_qs = StudentProfile.objects.select_related("user").filter(organization=report.organization)

    # Try admission_no first
    student = sp_qs.filter(admission_no__iexact=identifier).first()
    if not student:
        # Try email
        student = sp_qs.filter(user__email__iexact=identifier).first()

    if not student:
        return Response({"detail": "Student not found."}, status=status.HTTP_404_NOT_FOUND)

    user = student.user
    if not check_password(password, user.password):
        return Response({"detail": "Invalid password."}, status=status.HTTP_401_UNAUTHORIZED)

    # Check if student is a recipient
    recipient = report.recipients.filter(student=student).first()
    if not recipient:
        return Response({"detail": "This student is not a recipient of this report."}, status=status.HTTP_403_FORBIDDEN)

    # Check if parent exists
    links = ParentChildLink.objects.filter(student=student).select_related("parent__user")
    has_parent = links.exists()

    parent_email = None
    parent_session_token = None
    if has_parent:
        parent_user = links.first().parent.user
        parent_email = parent_user.email
        # Create a session token for the parent
        from api.models import SessionToken
        session_token_obj = SessionToken.create_for_user(parent_user, hours_valid=24)
        parent_session_token = session_token_obj.key

    return Response({
        "valid": True,
        "student_id": student.id,
        "student_name": (user.get_full_name() or user.email).strip(),
        "has_parent": has_parent,
        "parent_email": parent_email,
        "parent_session_token": parent_session_token,
        "needs_parent_setup": not has_parent,
        "report": _serialize_report_detail(report, student=student, request=request),
    })


@api_view(["POST"])
@permission_classes([HasAPIKey])
def public_parent_setup(request, token):
    """Create parent account from public report link."""
    report = TeacherReport.objects.filter(
        share_token=token, status="published"
    ).select_related("organization").first()
    if not report:
        return Response({"detail": "Report not found."}, status=status.HTTP_404_NOT_FOUND)

    data = request.data
    student_id = data.get("student_id")
    parent_email = (data.get("email") or "").strip().lower()
    use_same_password = data.get("use_same_password", False)
    new_password = data.get("new_password", "")
    student_password = data.get("student_password", "")

    if not student_id or not parent_email:
        return Response({"detail": "student_id and email required."}, status=status.HTTP_400_BAD_REQUEST)

    student = StudentProfile.objects.select_related("user").filter(
        id=student_id, organization=report.organization
    ).first()
    if not student:
        return Response({"detail": "Student not found."}, status=status.HTTP_404_NOT_FOUND)

    # Verify student password again
    if not check_password(student_password, student.user.password):
        return Response({"detail": "Invalid student password."}, status=status.HTTP_401_UNAUTHORIZED)

    # Check if email already exists
    existing_user = User.objects.filter(email__iexact=parent_email).first()

    with transaction.atomic():
        if existing_user:
            # Check if already a parent
            pp = ParentProfile.objects.filter(user=existing_user).first()
            if not pp:
                pp = ParentProfile.objects.create(
                    user=existing_user,
                    organization=report.organization,
                )
            # Create membership
            OrganizationMembership.objects.get_or_create(
                user=existing_user,
                organization=report.organization,
                role=OrganizationMembership.Role.PARENT,
                defaults={"is_active": True},
            )
            # Link
            ParentChildLink.objects.get_or_create(
                parent=pp, student=student,
                defaults={"relationship": "parent"},
            )
            parent_user = existing_user
        else:
            # Create new user
            password = student_password if use_same_password else new_password
            if not password:
                return Response({"detail": "Password required."}, status=status.HTTP_400_BAD_REQUEST)

            parent_user = User.objects.create_user(
                email=parent_email,
                password=password,
                first_name=data.get("first_name", ""),
                last_name=data.get("last_name", ""),
            )
            parent_user.primary_org = report.organization
            parent_user.save(update_fields=["primary_org"])

            pp = ParentProfile.objects.create(
                user=parent_user,
                organization=report.organization,
            )

            OrganizationMembership.objects.create(
                user=parent_user,
                organization=report.organization,
                role=OrganizationMembership.Role.PARENT,
                is_active=True,
            )

            ParentChildLink.objects.create(
                parent=pp,
                student=student,
                relationship="parent",
            )

    # Mark as viewed
    ReportRecipient.objects.filter(
        report=report, student=student
    ).update(parent_viewed=True, parent_viewed_at=timezone.now())

    # Create session token for the newly created parent
    from api.models import SessionToken
    session_token_obj = SessionToken.create_for_user(parent_user, hours_valid=24)
    parent_session_token = session_token_obj.key

    return Response({
        "detail": "Parent account created successfully.",
        "parent_email": parent_user.email,
        "parent_session_token": parent_session_token,
        "report": _serialize_report_detail(report, student=student, request=request),
    }, status=status.HTTP_201_CREATED)


# ─── Parent: Fetch Report by Share Token ────────────────────

@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def parent_report_by_token(request, token):
    """
    Authenticated parent fetches a specific published report by its share token.
    Verifies that at least one of the parent's linked children is a recipient.

    Optional query param: ?student_id=<id>  — view a specific child's data
    (used when multiple children are recipients of the same report).
    """
    user = request.user
    requested_student_id = request.query_params.get("student_id")

    pp = ParentProfile.objects.filter(user=user).first()
    if not pp:
        return Response({"detail": "Parent profile not found."}, status=status.HTTP_403_FORBIDDEN)

    report = TeacherReport.objects.filter(
        share_token=token, status="published"
    ).select_related("course", "organization", "teacher__user").first()
    if not report:
        return Response({"detail": "Report not found."}, status=status.HTTP_404_NOT_FOUND)

    # Get all linked children who are recipients of this report
    child_links = ParentChildLink.objects.filter(parent=pp).select_related(
        "student__user", "student__current_classroom"
    )
    child_ids = [link.student_id for link in child_links]

    recipient_qs = report.recipients.filter(student_id__in=child_ids).select_related(
        "student__user", "student__current_classroom"
    )
    if not recipient_qs.exists():
        return Response(
            {"detail": "None of your children are recipients of this report."},
            status=status.HTTP_403_FORBIDDEN,
        )

    # Build linked_children summary for the frontend switcher
    linked_children = []
    for r in recipient_qs:
        s = r.student
        linked_children.append({
            "id": s.id,
            "name": (s.user.get_full_name() or s.user.email).strip(),
            "admission_no": s.admission_no or "",
            "classroom": s.current_classroom.name if s.current_classroom else "",
        })

    # Determine which child's data to load
    target_recipient = None
    if requested_student_id:
        target_recipient = recipient_qs.filter(student_id=requested_student_id).first()
    if not target_recipient:
        target_recipient = recipient_qs.first()

    # Mark as viewed by parent
    report.recipients.filter(student=target_recipient.student).update(
        parent_viewed=True, parent_viewed_at=timezone.now()
    )

    data = _serialize_report_detail(report, student=target_recipient.student, request=request)
    data["linked_children"] = linked_children
    return Response(data)

