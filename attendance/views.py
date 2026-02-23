# attendance/views.py

from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes, authentication_classes

from rest_framework_api_key.permissions import HasAPIKey
from rest_framework.response import Response
from rest_framework import status
from django.utils.dateparse import parse_datetime
from datetime import date, datetime, timedelta

from .models import AttendanceSession, AttendanceRecord
from academics.models import StudentProfile
from learning.models import Course, Enrollment
from api.authentication import SessionTokenAuthentication

# ─────────────────────────────────────────
# 1. Mark / Update Attendance (manual)
# ─────────────────────────────────────────
@api_view(["POST"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def mark_attendance(request):
    """
    Teacher manually marks attendance for students in a course on a given date.

    Body:
    {
        "course_id": 1,
        "date": "2026-02-20",          # optional, defaults to today
        "topic": "Introduction",        # optional
        "records": [
            {"student_id": 10, "present": true,  "note": ""},
            {"student_id": 11, "present": false, "note": "Sick leave"}
        ]
    }
    """
    user = request.user

    try:
        teacher_profile = user.teacher_profile
    except Exception:
        return Response({"detail": "Teacher profile not found."}, status=status.HTTP_404_NOT_FOUND)

    course_id = request.data.get("course_id")
    if not course_id:
        return Response({"detail": "course_id is required."}, status=status.HTTP_400_BAD_REQUEST)

    try:
        course = Course.objects.get(
            id=course_id,
            teacher=teacher_profile,
            organization=teacher_profile.organization,
            is_active=True,
        )
    except Course.DoesNotExist:
        return Response({"detail": "Course not found or not yours."}, status=status.HTTP_404_NOT_FOUND)

    # Resolve attendance date
    raw_date = request.data.get("date")
    if raw_date:
        try:
            attendance_date = date.fromisoformat(raw_date)
        except ValueError:
            return Response({"detail": "Invalid date format. Use YYYY-MM-DD."}, status=status.HTTP_400_BAD_REQUEST)
    else:
        attendance_date = timezone.localdate()

    topic = request.data.get("topic", "")
    records_data = request.data.get("records", [])

    if not isinstance(records_data, list):
        return Response({"detail": "records must be a list."}, status=status.HTTP_400_BAD_REQUEST)

    # Get or create the session
    session, _ = AttendanceSession.objects.get_or_create(
        course=course,
        date=attendance_date,
        defaults={"topic": topic},
    )
    if topic:
        session.topic = topic
        session.save(update_fields=["topic"])

    # Validate student IDs against course enrollments
    enrolled_ids = set(
        Enrollment.objects.filter(
            course=course,
            status__in=[Enrollment.Status.ACTIVE, Enrollment.Status.COMPLETED],
        ).values_list("student_id", flat=True)
    )

    created, updated, skipped = 0, 0, 0
    errors = []

    for item in records_data:
        student_id = item.get("student_id")
        present = item.get("present", True)
        note = item.get("note", "")

        if student_id not in enrolled_ids:
            errors.append({"student_id": student_id, "error": "Not enrolled in this course."})
            skipped += 1
            continue

        record, was_created = AttendanceRecord.objects.update_or_create(
            session=session,
            student_id=student_id,
            defaults={"present": present, "note": note},
        )
        if was_created:
            created += 1
        else:
            updated += 1

    return Response(
        {
            "session_id": session.id,
            "date": attendance_date.isoformat(),
            "created": created,
            "updated": updated,
            "skipped": skipped,
            "errors": errors,
        },
        status=status.HTTP_200_OK,
    )


# ─────────────────────────────────────────
# 2. View Attendance Records for a Course
# ─────────────────────────────────────────
@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def course_attendance(request, course_id):
    """
    Returns all attendance sessions + records for a course.

    Query params:
        date        - filter to a single date (YYYY-MM-DD)
        start_date  - range start
        end_date    - range end
        student_id  - filter by student
    """
    user = request.user

    try:
        teacher_profile = user.teacher_profile
    except Exception:
        return Response({"detail": "Teacher profile not found."}, status=status.HTTP_404_NOT_FOUND)

    try:
        course = Course.objects.get(
            id=course_id,
            teacher=teacher_profile,
            organization=teacher_profile.organization,
        )
    except Course.DoesNotExist:
        return Response({"detail": "Course not found."}, status=status.HTTP_404_NOT_FOUND)

    sessions_qs = AttendanceSession.objects.filter(course=course).order_by("-date")

    # Optional filters
    single_date = request.query_params.get("date")
    start_date = request.query_params.get("start_date")
    end_date = request.query_params.get("end_date")
    student_id = request.query_params.get("student_id")

    if single_date:
        try:
            sessions_qs = sessions_qs.filter(date=date.fromisoformat(single_date))
        except ValueError:
            return Response({"detail": "Invalid date."}, status=status.HTTP_400_BAD_REQUEST)
    else:
        if start_date:
            try:
                sessions_qs = sessions_qs.filter(date__gte=date.fromisoformat(start_date))
            except ValueError:
                return Response({"detail": "Invalid start_date."}, status=status.HTTP_400_BAD_REQUEST)
        if end_date:
            try:
                sessions_qs = sessions_qs.filter(date__lte=date.fromisoformat(end_date))
            except ValueError:
                return Response({"detail": "Invalid end_date."}, status=status.HTTP_400_BAD_REQUEST)

    # Prefetch records (optionally filtered by student)
    records_qs = AttendanceRecord.objects.select_related("student__user")
    if student_id:
        records_qs = records_qs.filter(student_id=student_id)

    from django.db.models import Prefetch
    sessions_qs = sessions_qs.prefetch_related(
        Prefetch("records", queryset=records_qs)
    )

    data = []
    for session in sessions_qs:
        records = []
        for r in session.records.all():
            u = getattr(r.student, "user", None)
            records.append(
                {
                    "student_id": r.student_id,
                    "name": (u.get_full_name() or u.email) if u else f"student-{r.student_id}",
                    "present": r.present,
                    "note": r.note,
                }
            )

        total = len(records)
        present_count = sum(1 for r in records if r["present"])

        data.append(
            {
                "session_id": session.id,
                "date": session.date.isoformat(),
                "topic": session.topic,
                "total_students": total,
                "present": present_count,
                "absent": total - present_count,
                "records": records,
            }
        )

    return Response({"course_id": course_id, "sessions": data})


# ─────────────────────────────────────────
# 3. Auto-mark Attendance (online activity)
# ─────────────────────────────────────────
@api_view(["POST"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def auto_mark_attendance(request, course_id):
    """
    Automatically marks attendance based on user last_login.

    Body (option 1 - today):
        { "mode": "today" }

    Body (option 2 - time range):
        {
            "mode": "range",
            "start": "2026-02-20T08:00:00Z",
            "end":   "2026-02-20T17:00:00Z"
        }

    Optional:
        "date":  "2026-02-20"   - the attendance date to record (defaults to today)
        "topic": "Online class"
    """
    user = request.user

    try:
        teacher_profile = user.teacher_profile
    except Exception:
        return Response({"detail": "Teacher profile not found."}, status=status.HTTP_404_NOT_FOUND)

    try:
        course = Course.objects.get(
            id=course_id,
            teacher=teacher_profile,
            organization=teacher_profile.organization,
            is_active=True,
        )
    except Course.DoesNotExist:
        return Response({"detail": "Course not found."}, status=status.HTTP_404_NOT_FOUND)

    mode = request.data.get("mode", "today")
    topic = request.data.get("topic", "Auto (online)")

    # Determine the window for "was online"
    now = timezone.now()

    if mode == "today":
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        window_start = today_start
        window_end = now

    elif mode == "range":
        raw_start = request.data.get("start")
        raw_end = request.data.get("end")
        if not raw_start or not raw_end:
            return Response(
                {"detail": "start and end are required for range mode."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        window_start = parse_datetime(raw_start)
        window_end = parse_datetime(raw_end)
        if not window_start or not window_end:
            return Response({"detail": "Invalid start or end datetime."}, status=status.HTTP_400_BAD_REQUEST)
        if timezone.is_naive(window_start):
            window_start = timezone.make_aware(window_start)
        if timezone.is_naive(window_end):
            window_end = timezone.make_aware(window_end)
        if window_start >= window_end:
            return Response({"detail": "start must be before end."}, status=status.HTTP_400_BAD_REQUEST)
    else:
        return Response({"detail": "mode must be 'today' or 'range'."}, status=status.HTTP_400_BAD_REQUEST)

    # Attendance date to record against
    raw_date = request.data.get("date")
    if raw_date:
        try:
            attendance_date = date.fromisoformat(raw_date)
        except ValueError:
            return Response({"detail": "Invalid date format."}, status=status.HTTP_400_BAD_REQUEST)
    else:
        attendance_date = timezone.localdate()

    # All enrolled (active) students in this course
    enrolled_students = list(
        StudentProfile.objects.filter(
            enrollments__course=course,
            enrollments__status=Enrollment.Status.ACTIVE,
        )
        .select_related("user")
        .distinct()
    )

    session, _ = AttendanceSession.objects.get_or_create(
        course=course,
        date=attendance_date,
        defaults={"topic": topic},
    )

    marked_present, marked_absent = 0, 0

    for student in enrolled_students:
        u = student.user
        last_login = getattr(u, "last_login", None)

        # Present if last_login falls within the window
        was_online = bool(last_login and window_start <= last_login <= window_end)

        AttendanceRecord.objects.update_or_create(
            session=session,
            student=student,
            defaults={"present": was_online, "note": "auto" if was_online else "auto-absent"},
        )

        if was_online:
            marked_present += 1
        else:
            marked_absent += 1

    return Response(
        {
            "session_id": session.id,
            "date": attendance_date.isoformat(),
            "mode": mode,
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "marked_present": marked_present,
            "marked_absent": marked_absent,
            "total": marked_present + marked_absent,
        }
    )



# Add to attendance/views.py

@api_view(["DELETE"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def delete_attendance_session(request, session_id):
    """
    Deletes an AttendanceSession (and all its records via CASCADE)
    only if it belongs to a course owned by the requesting teacher.
    """
    try:
        teacher_profile = request.user.teacher_profile
    except Exception:
        return Response({"detail": "Teacher profile not found."}, status=status.HTTP_404_NOT_FOUND)

    try:
        session = AttendanceSession.objects.select_related("course__teacher").get(id=session_id)
    except AttendanceSession.DoesNotExist:
        return Response({"detail": "Session not found."}, status=status.HTTP_404_NOT_FOUND)

    # Ownership check — session's course must belong to this teacher
    if session.course.teacher_id != teacher_profile.id:
        return Response({"detail": "You do not have permission to delete this session."}, status=status.HTTP_403_FORBIDDEN)

    session.delete()
    return Response({"detail": "Session deleted successfully."}, status=status.HTTP_200_OK)


