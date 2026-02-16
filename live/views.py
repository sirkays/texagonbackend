import json
import traceback

from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.timezone import is_naive, make_aware

from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.response import Response
from rest_framework import status

from api.authentication import SessionTokenAuthentication  # adjust import path
from rest_framework_api_key.permissions import HasAPIKey  # adjust import path
from .models import LiveSession  # adjust app label if different
from academics.models import TeacherProfile  # adjust if needed
from learning.models import Course,Enrollment
from core.utils import _get_teacher_for_user, _get_student_for_user
from api.permissions import RequiresActiveStudentSubscription
from texagonbackend.settings import FRONTEND_ORIGIN

@api_view(["POST"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def create_live_session(request):
    """
    Create a LiveSession for a course.
    Only the teacher assigned to that course may create the LiveSession.

    Expected POST body (JSON):
    {
        "course_id": <int>,               # required
        "title": "<string>",              # required
        "scheduled_at": "<ISO datetime>", # required (e.g. "2025-09-11T14:00:00Z" or local naive datetime)
        "duration_minutes": <int>,        # optional, defaults to 60
        "join_url": "<url>",              # optional
        "recording_url": "<url>",         # optional
        "meta": {...}                     # optional, object or JSON-string
        "active": true|false              # optional
    }
    """
    try:
        user = request.user
        teacher = _get_teacher_for_user(user)
        if not teacher:
            return Response({"detail": "Teacher profile not found."}, status=status.HTTP_403_FORBIDDEN)

        data = request.data

        # required fields
        course_id = data.get("course_id")
        title = data.get("title")
        scheduled_at_raw = data.get("scheduled_at")

        if not course_id:
            return Response({"detail": "course_id is required."}, status=status.HTTP_400_BAD_REQUEST)
        if not title:
            return Response({"detail": "title is required."}, status=status.HTTP_400_BAD_REQUEST)
        if not scheduled_at_raw:
            return Response({"detail": "scheduled_at is required."}, status=status.HTTP_400_BAD_REQUEST)

        # fetch course and ensure the teacher is the assigned teacher for that course
        try:
            course = Course.objects.select_related("teacher").get(pk=course_id)
        except Course.DoesNotExist:
            return Response({"detail": "Course not found."}, status=status.HTTP_404_NOT_FOUND)

        if course.teacher_id != teacher.id:
            # The currently authenticated teacher is not assigned to this course
            return Response({"detail": "You are not authorized to create a live session for this course."},
                            status=status.HTTP_403_FORBIDDEN)

        # parse scheduled_at into an aware datetime
        scheduled_dt = parse_datetime(scheduled_at_raw)
        if scheduled_dt is None:
            return Response({"detail": "scheduled_at must be a valid ISO datetime string."},
                            status=status.HTTP_400_BAD_REQUEST)

        # make aware if naive, using current timezone
        if is_naive(scheduled_dt):
            scheduled_dt = make_aware(scheduled_dt, timezone=timezone.get_current_timezone())

        # duration
        duration = data.get("duration_minutes", 60)
        try:
            duration = int(duration)
            if duration <= 0:
                raise ValueError()
        except (ValueError, TypeError):
            return Response({"detail": "duration_minutes must be a positive integer."},
                            status=status.HTTP_400_BAD_REQUEST)

        # optional fields
        join_url = data.get("join_url", "") or ""
        recording_url = data.get("recording_url", "") or ""
        active = data.get("active", True)

        # meta: accept dict or JSON string
        meta_raw = data.get("meta", {})
        if isinstance(meta_raw, str):
            try:
                meta = json.loads(meta_raw)
            except json.JSONDecodeError:
                return Response({"detail": "meta must be a valid JSON object or JSON string."},
                                status=status.HTTP_400_BAD_REQUEST)
        elif isinstance(meta_raw, dict):
            meta = meta_raw
        else:
            # allow null -> {}
            meta = {}

        join_url = f"{FRONTEND_ORIGIN}/{join_url}"
        # create the LiveSession; host should be the authenticated teacher
        live_session = LiveSession.objects.create(
            course=course,
            title=title,
            scheduled_at=scheduled_dt,
            duration_minutes=duration,
            host=teacher,
            join_url=join_url,
            recording_url=recording_url,
            meta=meta,
            active=bool(active),
        )

        # response payload
        payload = {
            "id": live_session.id,
            "course_id": live_session.course_id,
            "title": live_session.title,
            "scheduled_at": live_session.scheduled_at.isoformat(),
            "duration_minutes": live_session.duration_minutes,
            "host_id": live_session.host_id,
            "join_url": live_session.join_url,
            "recording_url": live_session.recording_url,
            "meta": live_session.meta,
            "active": live_session.active,
            "created_at": getattr(live_session, "created", None) or getattr(live_session, "date_created", None)
        }

        return Response(payload, status=status.HTTP_201_CREATED)

    except Exception as e:
        payload = {
            "detail": "Error creating live session.",
            "error": f"{type(e).__name__}: {e}",
        }
        if request.query_params.get("debug") in {"1", "true"} or getattr(settings, "DEBUG", False):
            payload["traceback"] = traceback.format_exc()
        return Response(payload, status=status.HTTP_500_INTERNAL_SERVER_ERROR)






@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def user_live_sessions(request):
    """
    Get live sessions for the authenticated student or teacher.

    - If `course_id` is provided as a query param, only sessions for that course are returned.
    - For students: sessions come from their active enrollments.
    - For teachers: sessions come from the courses they teach.

    Example:
    GET /api/live-sessions/              -> all sessions
    GET /api/live-sessions/?course_id=5  -> sessions for course 5
    """
    try:
        user = request.user
        student = _get_student_for_user(user)
        teacher = None if student else _get_teacher_for_user(user)
        if not student and not teacher:
            return Response(
                {"detail": "Student or Teacher profile not found."},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Optional course filter
        course_id = request.query_params.get("course_id")

        # Base queryset
        if student:
            enrollments = Enrollment.objects.filter(
                student=student,
                status=Enrollment.Status.ACTIVE
            ).select_related(
                "course__subject", "course__classroom", "course__teacher__user"
            )

            if course_id:
                enrollments = enrollments.filter(course_id=course_id)

            if not enrollments.exists():
                return Response({"live_sessions": []}, status=status.HTTP_200_OK)

            course_ids = [en.course_id for en in enrollments]

        else:  # teacher
            courses = Course.objects.filter(teacher=teacher, is_active=True)

            if course_id:
                courses = courses.filter(id=course_id)

            if not courses.exists():
                return Response({"live_sessions": []}, status=status.HTTP_200_OK)

            course_ids = list(courses.values_list("id", flat=True))
        # Fetch live sessions
        live_sessions = (
            LiveSession.objects.filter(course_id__in=course_ids, active=True)
            .select_related("course__subject", "course__classroom", "course__teacher__user", "host__user")
            .order_by("scheduled_at")
        )
        sessions_data = []
        for session in live_sessions:
            sessions_data.append({
                "id": session.id,
                "title": session.title,
                "scheduled_at": session.scheduled_at.isoformat(),
                "duration_minutes": session.duration_minutes,
                "join_url": session.join_url,
                "recording_url": session.recording_url,
                "course": {
                    "id": session.course.id,
                    "name": getattr(session.course, "name", ""),
                    "subject": getattr(session.course.subject, "name", ""),
                    "classroom": getattr(session.course.classroom, "name", ""),
                    "teacher": getattr(session.course.teacher.user, "email", ""),
                },
                "host": getattr(session.host.user, "email", ""),
                "active": session.active,
                "status": session.status,
            })

        return Response({"live_sessions": sessions_data}, status=status.HTTP_200_OK)

    except Exception as e:
        print(e)
        payload = {
            "detail": "Error fetching live sessions.",
            "error": f"{type(e).__name__}: {e}",
        }
        if request.query_params.get("debug") in {"1", "true"} or getattr(settings, "DEBUG", False):
            payload["traceback"] = traceback.format_exc()
        return Response(payload, status=status.HTTP_500_INTERNAL_SERVER_ERROR)





@api_view(["PATCH"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def update_live_session_status(request, session_id):
    """
    Update the status of a LiveSession.
    Only the teacher assigned to the course may update it.

    Example PATCH body:
    {
        "status": "completed"   # pending | started | completed | cancelled
    }
    """
    try:
        user = request.user
        teacher = _get_teacher_for_user(user)
        if not teacher:
            return Response({"detail": "Teacher profile not found."},
                            status=status.HTTP_403_FORBIDDEN)

        # Find live session
        try:
            live_session = LiveSession.objects.select_related("course__teacher").get(pk=session_id)
        except LiveSession.DoesNotExist:
            return Response({"detail": "Live session not found."}, status=status.HTTP_404_NOT_FOUND)

        # Ensure the teacher is assigned to the course
        if live_session.course.teacher_id != teacher.id:
            return Response({"detail": "You are not authorized to update this live session."},
                            status=status.HTTP_403_FORBIDDEN)

        # Validate status
        new_status = request.data.get("status")
        valid_choices = dict(LiveSession.Status.choices).keys()
        if not new_status or new_status not in valid_choices:
            return Response({
                "detail": f"Invalid status. Must be one of: {list(valid_choices)}"
            }, status=status.HTTP_400_BAD_REQUEST)

        # Update
        live_session.status = new_status
        live_session.save(update_fields=["status"])

        payload = {
            "id": live_session.id,
            "title": live_session.title,
            "course_id": live_session.course_id,
            "status": live_session.status,
            "scheduled_at": live_session.scheduled_at.isoformat(),
            "duration_minutes": live_session.duration_minutes,
            "updated": getattr(live_session, "modified", None)  # TimeStampedModel usually has modified/updated
        }

        return Response(payload, status=status.HTTP_200_OK)

    except Exception as e:
        payload = {
            "detail": "Error updating live session status.",
            "error": f"{type(e).__name__}: {e}",
        }
        if request.query_params.get("debug") in {"1", "true"} or getattr(settings, "DEBUG", False):
            payload["traceback"] = traceback.format_exc()
        return Response(payload, status=status.HTTP_500_INTERNAL_SERVER_ERROR)



@api_view(["DELETE"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def delete_live_session(request, session_id):
    """
    Delete a LiveSession.
    Only the teacher assigned to the course of the session may delete it.

    Example:
    DELETE /api/live-sessions/12/
    """
    try:
        user = request.user
        teacher = _get_teacher_for_user(user)
        if not teacher:
            return Response({"detail": "Teacher profile not found."},
                            status=status.HTTP_403_FORBIDDEN)

        # Find session
        try:
            live_session = LiveSession.objects.select_related("course__teacher").get(pk=session_id)
        except LiveSession.DoesNotExist:
            return Response({"detail": "Live session not found."}, status=status.HTTP_404_NOT_FOUND)

        # Check ownership
        if live_session.course.teacher_id != teacher.id:
            return Response({"detail": "You are not authorized to delete this live session."},
                            status=status.HTTP_403_FORBIDDEN)

        # Delete session
        live_session.delete()

        return Response({"detail": f"Live session {session_id} deleted successfully."},
                        status=status.HTTP_204_NO_CONTENT)

    except Exception as e:
        payload = {
            "detail": "Error deleting live session.",
            "error": f"{type(e).__name__}: {e}",
        }
        if request.query_params.get("debug") in {"1", "true"} or getattr(settings, "DEBUG", False):
            payload["traceback"] = traceback.format_exc()
        return Response(payload, status=status.HTTP_500_INTERNAL_SERVER_ERROR)



@api_view(["PATCH"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def update_live_session(request, session_id):
    """
    Update a LiveSession (partial update).
    Only the teacher assigned to the course may update it.

    Example PATCH body:
    {
        "title": "New Title",
        "scheduled_at": "2025-09-12T14:00:00Z",
        "duration_minutes": 90,
        "join_url": "https://zoom.us/new-link",
        "active": false
    }
    """
    try:
        user = request.user
        teacher = _get_teacher_for_user(user)
        if not teacher:
            return Response({"detail": "Teacher profile not found."},
                            status=status.HTTP_403_FORBIDDEN)

        # Get session
        try:
            live_session = LiveSession.objects.select_related("course__teacher").get(pk=session_id)
        except LiveSession.DoesNotExist:
            return Response({"detail": "Live session not found."}, status=status.HTTP_404_NOT_FOUND)

        # Check if teacher is assigned
        if live_session.course.teacher_id != teacher.id:
            return Response({"detail": "You are not authorized to update this live session."},
                            status=status.HTTP_403_FORBIDDEN)

        # Allowed fields to update
        allowed_fields = [
            "title", "scheduled_at", "duration_minutes",
            "join_url", "recording_url", "meta", "active"
        ]

        data = request.data
        updated = False

        for field in allowed_fields:
            if field in data:
                setattr(live_session, field, data[field])
                updated = True

        if updated:
            live_session.save()
        else:
            return Response({"detail": "No valid fields provided for update."},
                            status=status.HTTP_400_BAD_REQUEST)

        payload = {
            "id": live_session.id,
            "title": live_session.title,
            "scheduled_at": live_session.scheduled_at.isoformat(),
            "duration_minutes": live_session.duration_minutes,
            "join_url": live_session.join_url,
            "recording_url": live_session.recording_url,
            "meta": live_session.meta,
            "status": live_session.status,
            "active": live_session.active,
            "course": {
                "id": live_session.course.id,
                "name": live_session.course.name,
                "teacher": getattr(live_session.course.teacher.user, "email", ""),
            }
        }

        return Response(payload, status=status.HTTP_200_OK)

    except Exception as e:
        payload = {
            "detail": "Error updating live session.",
            "error": f"{type(e).__name__}: {e}",
        }
        if request.query_params.get("debug") in {"1", "true"} or getattr(settings, "DEBUG", False):
            payload["traceback"] = traceback.format_exc()
        return Response(payload, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
