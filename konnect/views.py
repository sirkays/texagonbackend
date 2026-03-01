from django.shortcuts import render
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from rest_framework_api_key.permissions import HasAPIKey

from api.authentication import SessionTokenAuthentication

from core.utils import _get_teacher_for_user, _resolve_org

from .models import KonnectRoom,KonnectRoomUser

from texagonbackend.settings import KONNECT_LOGOUT_URL

from .konn3ct_client import Konn3ctAPI

from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework import status
from django.core.exceptions import ObjectDoesNotExist
from academics.models import StudentProfile, TeacherProfile  # adjust if import path differs
from learning.models import Enrollment, Course
from accounts.models import User

TOKEN = "H9LBxlRkBWs2hb1mnZ0v2wzfhOqwfjCFaK73Jx99"

konn3ct = Konn3ctAPI(TOKEN)


# Helper serializer-ish functions
def _course_to_payload(course: Course):
    return {
        "id": course.id,
        "name": getattr(course, "name", None) or str(course),
        "subject": getattr(course.subject, "name", None),
        "teacher_id": getattr(course.teacher, "id", None),
    }

def _user_to_payload(user: User):
    return {
        "id": user.id,
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
    }


@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def get_room_allowed(request):
    """
    Returns allowed_courses and allowed_users for a room.
    GET params:
      - room_id (required)
    Response:
      {
        "room_id": "...",
        "allowed_courses": [{id,name,subject,teacher_id}, ...],
        "allowed_users": [{id,email,first_name,last_name}, ...]
      }
    """
    room_id = request.GET.get("room_id")
    if not room_id:
        return Response({"detail": "room_id is required."}, status=status.HTTP_400_BAD_REQUEST)

    try:
        konnect_room = KonnectRoom.objects.get(room_id=room_id)
    except KonnectRoom.DoesNotExist:
        return Response({"detail": "Room not found."}, status=status.HTTP_404_NOT_FOUND)

    courses = [ _course_to_payload(c) for c in konnect_room.allowed_courses.all() ]
    users = [ _user_to_payload(u) for u in konnect_room.allowed_users.all() ]

    return Response({
        "room_id": konnect_room.room_id,
        "allowed_courses": courses,
        "allowed_users": users,
        "status": konnect_room.status,
        "name": konnect_room.name,
    }, status=status.HTTP_200_OK)



@api_view(["POST"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def start_room(request):
    """
    Starts (or re-uses) a KonnectRoom and optionally adds/removes courses and users.
    When calling konn3ct.start_room we only send the saved/sourced allowed_courses and allowed_users.
    Request body may include:
      - name, message/welcome_message
      - add_course_ids, remove_course_ids, add_user_ids, remove_user_ids
    """
    user = request.user
    teacher = _get_teacher_for_user(user)

    if not teacher:
        return Response({"detail": "Teacher profile not found."}, status=status.HTTP_404_NOT_FOUND)

    org, err = _resolve_org(request)
    if err:
        return err

    konnect_rooms_qs = user.konnect_rooms.exclude(status="disabled")

    payload = request.data or {}
    name = payload.get("name", f"{user.first_name}'s room")
    welcome_message = payload.get("message") or payload.get("welcome_message") or ""

    def _to_id_list(val):
        if val is None:
            return []
        if isinstance(val, str):
            return [v.strip() for v in val.split(",") if v.strip()]
        if isinstance(val, (list, tuple)):
            return [v for v in val if v is not None]
        return [val]

    def _to_ints(id_list):
        out = []
        for i in id_list:
            try:
                out.append(int(i))
            except (ValueError, TypeError):
                continue
        return out

    add_course_ids = _to_ints(_to_id_list(payload.get("add_course_ids")))
    remove_course_ids = _to_ints(_to_id_list(payload.get("remove_course_ids")))
    add_user_ids = _to_ints(_to_id_list(payload.get("add_user_ids")))
    remove_user_ids = _to_ints(_to_id_list(payload.get("remove_user_ids")))

    try:
        with transaction.atomic():
            # Create or reuse KonnectRoom
            if konnect_rooms_qs.exists():
                konnect_room = konnect_rooms_qs.first()
                room_id = konnect_room.room_id
                konnect_room.name = name
                if welcome_message:
                    konnect_room.welcome_message = welcome_message
                konnect_room.save()
            else:
                room = konn3ct.create_room(
                    name=name,
                    logout_url=KONNECT_LOGOUT_URL,
                    welcome_message=welcome_message,
                )
                if not (room.get("data") and room["data"].get("id")):
                    return Response({"detail": "Cannot start meeting."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
                room_id = room["data"]["id"]

                konnect_room = KonnectRoom.objects.create(
                    name=name,
                    creator=user,
                    room_id=room_id,
                    welcome_message=welcome_message
                )

            # Apply add/remove operations (same semantics as before)
            changes = {
                "courses_added": [], "courses_removed": [],
                "users_added": [], "users_removed": [],
                "invalid_course_ids": [], "invalid_user_ids": []
            }

            for cid in add_course_ids:
                try:
                    course = Course.objects.get(pk=cid)
                    # Optionally ensure the teacher owns the course before allowing add:
                    # if course.teacher_id != teacher.id: raise PermissionError(...)
                    konnect_room.allowed_courses.add(course)
                    changes["courses_added"].append(cid)
                except Course.DoesNotExist:
                    changes["invalid_course_ids"].append(cid)

            for cid in remove_course_ids:
                try:
                    course = Course.objects.get(pk=cid)
                    konnect_room.allowed_courses.remove(course)
                    changes["courses_removed"].append(cid)
                except Course.DoesNotExist:
                    changes["invalid_course_ids"].append(cid)

            for uid in add_user_ids:
                try:
                    u = User.objects.get(pk=uid)
                    konnect_room.allowed_users.add(u)
                    changes["users_added"].append(uid)
                    kru, created = KonnectRoomUser.objects.get_or_create(
                        user=u, konnect_room=konnect_room,
                        defaults={"active": True, "name": getattr(u, "get_full_name", lambda: str(u))()}
                    )
                    if not created:
                        kru.active = True
                        kru.save(update_fields=["active"])
                except User.DoesNotExist:
                    changes["invalid_user_ids"].append(uid)

            for uid in remove_user_ids:
                try:
                    u = User.objects.get(pk=uid)
                    konnect_room.allowed_users.remove(u)
                    changes["users_removed"].append(uid)
                    try:
                        kru = KonnectRoomUser.objects.get(user=u, konnect_room=konnect_room)
                        kru.active = False
                        kru.save(update_fields=["active"])
                    except KonnectRoomUser.DoesNotExist:
                        pass
                except User.DoesNotExist:
                    changes["invalid_user_ids"].append(uid)

            # Persist status open
            konnect_room.status = KonnectRoom.STAT.OPEN
            konnect_room.save()

            # Build canonical allowed lists to send to external service
            allowed_courses_qs = konnect_room.allowed_courses.all()
            allowed_users_qs = konnect_room.allowed_users.all()

            allowed_courses_payload = [
                _course_to_payload(c) for c in allowed_courses_qs
            ]
            allowed_users_payload = [
                _user_to_payload(u) for u in allowed_users_qs
            ]

            # Finally call external start API with only the saved allowed lists
            start_payload = {
                "room_id": konnect_room.room_id,
                "name": konnect_room.name,
                "started_by": f"{user.first_name} {user.last_name}",
                "message": "Session started",
                "logout_url": KONNECT_LOGOUT_URL,
                "allowed_courses": allowed_courses_payload,
                "allowed_users": allowed_users_payload,
            }

            start = konn3ct.start_room(**start_payload)

            # If the external API returned an error, raise to rollback changes (optional)
            if not start or not start.get("data"):
                # If you'd rather keep local changes even if external fails, change this behavior.
                raise Exception("External start_room call failed: " + str(start))

    except Exception as exc:
        return Response({"detail": "Failed to start/update room.", "error": str(exc)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    summary = {
        "detail": f"Room '{konnect_room.name}' started successfully.",
        "room_id": konnect_room.room_id,
        "status": konnect_room.status,
        "changes": changes,
        "allowed_courses_sent": allowed_courses_payload,
        "allowed_users_sent": allowed_users_payload,
        "external_response": start,
    }

    return Response(summary, status=status.HTTP_200_OK)


@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def join_room(request):
    """
    Join a konnect room only if:
      - room exists and is open
      - user is the creator OR user is in konnect_room.allowed_users OR
        user is a teacher for one of the allowed_courses OR
        user is a student currently enrolled (status=active and not completed) in one of the allowed_courses
    If konnect_room.allowed_courses is empty, we treat the room as open to allowed_users + creator + teachers + active enrolled students.
    """
    user = request.user
    room_id = request.GET.get("room_id")
    if not room_id:
        return Response({"detail": "room_id is required."}, status=status.HTTP_400_BAD_REQUEST)

    try:
        konnect_room = KonnectRoom.objects.get(room_id=room_id, status=KonnectRoom.STAT.OPEN)
    except ObjectDoesNotExist:
        return Response({"detail": "Room has not started."}, status=status.HTTP_404_NOT_FOUND)

    # Immediate allow: room creator
    if user == konnect_room.creator:
        allowed = True
    # Immediate allow: explicitly allowed users
    elif konnect_room.allowed_users.filter(pk=user.pk).exists():
        allowed = True
    else:
        allowed = False
        # If room has allowed_courses set, restrict by those. If none are set, fall back to other checks below.
        allowed_course_qs = konnect_room.allowed_courses.all()
        # If the room has allowed_courses defined (non-empty), require the user to be related to at least one of them.
        if allowed_course_qs.exists():
            # 1) If user is a teacher, allow if they teach any of the allowed courses
            try:
                teacher_profile = getattr(user, "teacher_profile", None)
                if teacher_profile:
                    if Course.objects.filter(pk__in=allowed_course_qs.values_list("pk", flat=True),
                                             teacher=teacher_profile).exists():
                        allowed = True
            except Exception:
                # ignore and continue checking student path
                allowed = allowed or False

            # 2) Student path: allow if student has active (non-completed) enrollment for any allowed course
            try:
                student_profile = getattr(user, "student_profile", None)
                if student_profile:
                    # active enrollments for allowed courses (status ACTIVE and completed_at is null)
                    if Enrollment.objects.filter(
                        student=student_profile,
                        course__in=allowed_course_qs,
                        status=Enrollment.Status.ACTIVE,
                        completed_at__isnull=True
                    ).exists():
                        allowed = True
            except Exception:
                allowed = allowed or False

        else:
            # No allowed_courses defined on the room => allow teachers or students with any active enrollments in org
            # (depending on your policy; below we allow any teacher or active enrolled student)
            try:
                teacher_profile = getattr(user, "teacher_profile", None)
                if teacher_profile:
                    allowed = True
            except Exception:
                pass

            if not allowed:
                try:
                    student_profile = getattr(user, "student_profile", None)
                    if student_profile:
                        if Enrollment.objects.filter(
                            student=student_profile,
                            status=Enrollment.Status.ACTIVE,
                            completed_at__isnull=True
                        ).exists():
                            allowed = True
                except Exception:
                    pass

    if not allowed:
        return Response(
            {"detail": "You are not permitted to join this room — either you are not enrolled in any allowed course or you have completed the allowed course(s)."},
            status=status.HTTP_403_FORBIDDEN
        )

    # At this point the user is allowed; proceed to call konn3ct
    try:
        join = konn3ct.join_room(
            room_id=room_id,
            name=f"{user.first_name} {user.last_name}",
            email=f"{user.email}",
            role="moderator" if user == konnect_room.creator else "viewer"
        )
    except Exception as exc:
        return Response({"detail": "Failed to call join service.", "error": str(exc)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    if join.get("data"):
        # Optionally create or update KonnectRoomUser to track this join
        try:
            kru, created = KonnectRoomUser.objects.get_or_create(
                user=user,
                konnect_room=konnect_room,
                defaults={"active": True, "name": getattr(user, "get_full_name", lambda: str(user))()}
            )
            if not created:
                kru.active = True
                kru.save(update_fields=["active", "updated_at"])
        except Exception:
            # non-fatal; we still allow join even if room-user tracking fails
            pass

        return Response({"detail": "You have joined the room.", "link": join["data"]}, status=status.HTTP_200_OK)

    return Response({"detail": "Cannot join meeting."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)