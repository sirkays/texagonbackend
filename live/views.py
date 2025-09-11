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
from learning.models import Course
from core.utils import _get_teacher_for_user

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
