from django.shortcuts import render
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db.models import Prefetch
import time
from rest_framework_api_key.permissions import HasAPIKey

from api.authentication import SessionTokenAuthentication

from core.utils import _get_teacher_for_user, _resolve_org

from .models import KonnectRoom,KonnectRoomUser

from texagonbackend.settings import KONNECT_LOGOUT_URL

from .konn3ct_client import Konn3ctAPI

from django.db.models import Q, Count
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework import status
from django.core.exceptions import ObjectDoesNotExist
from academics.models import StudentProfile, TeacherProfile  # adjust if import path differs
from learning.models import Enrollment, Course
from accounts.models import User
from .serializers import KonnectRoomListSerializer
from .utils import check_room_delete,save_last_update
from texagonbackend.settings import KONNECT_TOKEN

TOKEN = KONNECT_TOKEN

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
def start_konnect_room(request):
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
                data, res = check_room_delete(konn3ct)
                if res is False and data is None:
                    return Response({"detail": "You cannot start room now because the lobby is full."}, 
                                    status=status.HTTP_500_INTERNAL_SERVER_ERROR)

                room = konn3ct.create_room(
                    name=name,
                    logout_url=KONNECT_LOGOUT_URL,
                    welcome_message=welcome_message,
                )
                if not (room.get("data") and room["data"].get("id")):
                    return Response({"detail": "Cannot start meeting."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
                room_id = room["data"]["id"]
                if res:
                    konnect_room.name = name
                    konnect_room.creator = user
                    konnect_room.room_id = room_id
                    konnect_room.welcome_message = welcome_message
                    konnect_room.allowed_courses = None
                    konnect_room.allowed_users = None
                    konnect_room.save()
                else:
                    konnect_room = KonnectRoom.objects.create(
                        name=name,
                        creator=user,
                        room_id=room_id,
                        welcome_message=welcome_message
                    )
                save_last_update(konnect_room)

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
            save_last_update(konnect_room)
            print(start, " start")
            # If the external API returned an error, raise to rollback changes (optional)
            if start.get("success") is False:
                # If you'd rather keep local changes even if external fails, change this behavior.
                raise Exception("External start_room call failed: " + str(start))

    except Exception as exc:
        print(exc)
        return Response({"detail": "Failed to start/update room.", "error": str(exc)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    try:
        summary = {
            "detail": f"Room '{konnect_room.name}' started successfully.",
            "room_id": konnect_room.room_id,
            "status": konnect_room.status,
            "changes": changes,
            "allowed_courses_sent": allowed_courses_payload,
            "allowed_users_sent": allowed_users_payload,
            "external_response": start,
        }
    except Exception as e:
        print(e)
    return Response(summary, status=status.HTTP_200_OK)



@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def join_room(request):
    """
    Join a konnect room.

    Behavior:
    - Try to join via konn3ct.join_room().
    - If the external service returns the "room not started" message AND the
      requester is the room creator, attempt to start the room via konn3ct.start_room()
      (using canonical allowed lists), mark the room OPEN locally, then retry join.
    - If the external service returns "room not started" and requester is not the
      creator => return a 'room has not started' response.
    - Handles permission checks (creator / allowed users / teacher / enrolled student).
    """
    user = request.user
    room_id = request.GET.get("room_id")
    if not room_id:
        return Response({"detail": "room_id is required."}, status=status.HTTP_400_BAD_REQUEST)

    # Get room regardless of status (we may need to start it)
    try:
        konnect_room = KonnectRoom.objects.get(room_id=room_id)
    except KonnectRoom.DoesNotExist:
        return Response({"detail": "Room not found."}, status=status.HTTP_404_NOT_FOUND)

    # Permission checks (same logic as before)
    allowed = False

    if user == konnect_room.creator:
        allowed = True
    elif konnect_room.allowed_users.filter(pk=user.pk).exists():
        allowed = True
    else:
        allowed_course_qs = konnect_room.allowed_courses.all()
        if allowed_course_qs.exists():
            # teacher path
            try:
                teacher_profile = getattr(user, "teacher_profile", None)
                if teacher_profile:
                    if Course.objects.filter(
                        pk__in=allowed_course_qs.values_list("pk", flat=True),
                        teacher=teacher_profile
                    ).exists():
                        allowed = True
            except Exception:
                pass

            # student path
            try:
                student_profile = getattr(user, "student_profile", None)
                if student_profile:
                    if Enrollment.objects.filter(
                        student=student_profile,
                        course__in=allowed_course_qs,
                        status=Enrollment.Status.ACTIVE,
                        completed_at__isnull=True
                    ).exists():
                        allowed = True
            except Exception:
                pass
        else:
            # No allowed_courses: allow any teacher or any active enrolled student
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

    # Helper to call external join
    def _external_join_as(user, role):
        
        return konn3ct.join_room(
            room_id=room_id,
            name=f"{user.first_name} {user.last_name}",
            email=f"{user.email}",
            role=role
        )

    # First attempt to join
    try:
        role = "moderator" if user == konnect_room.creator else "viewer"
        join_resp = _external_join_as(user, role)
    except Exception as exc:
        return Response({"detail": "Failed to call join service.", "error": str(exc)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    # If join succeeded return link
    if join_resp.get("data"):
        try:
            save_last_update(konnect_room)
            kru, created = KonnectRoomUser.objects.get_or_create(
                user=user,
                konnect_room=konnect_room,
                defaults={"active": True, "name": getattr(user, "get_full_name", lambda: str(user))()}
            )
            if not created:
                kru.active = True
                kru.save(update_fields=["active", "updated_at"])
        except Exception:
            pass

        return Response({"detail": "You have joined the room.", "link": join_resp["data"]}, status=status.HTTP_200_OK)

    # If join failed with the known "room not started" message, handle according to creator vs non-creator
    failure_message = (join_resp.get("message") or "") if isinstance(join_resp, dict) else ""
    room_not_started_indicators = [
        "Unable to join. Kindly confirm the room is started",
        "room has not started",
        "Moderator has joined"
    ]
    is_room_not_started = any(indicator.lower() in failure_message.lower() for indicator in room_not_started_indicators)

    if is_room_not_started:
        # If requester is creator -> attempt start + retry join
        if user == konnect_room.creator:
            # Build canonical allowed payloads from DB
            allowed_courses_qs = konnect_room.allowed_courses.all()
            allowed_users_qs = konnect_room.allowed_users.all()

            allowed_courses_payload = [_course_to_payload(c) for c in allowed_courses_qs]
            allowed_users_payload = [_user_to_payload(u) for u in allowed_users_qs]

            start_payload = {
                "room_id": konnect_room.room_id,
                "name": konnect_room.name or f"{user.first_name}'s room",
                "started_by": f"{user.first_name} {user.last_name}",
                "message": "Session started by creator (auto-start via join)",
                "logout_url": KONNECT_LOGOUT_URL,
                "allowed_courses": allowed_courses_payload,
                "allowed_users": allowed_users_payload,
            }

            try:
                start_resp = konn3ct.start_room(**start_payload)
            except Exception as exc:
                return Response({"detail": "Failed to call start service.", "error": str(exc)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

            # If start failed, surface external response
            if start_resp.get("success") is False:
                return Response(
                    {"detail": "Failed to start room on external service.", "external": start_resp},
                    status=status.HTTP_502_BAD_GATEWAY
                )

            # Persist local OPEN status (best-effort)
            try:
                konnect_room.status = KonnectRoom.STAT.OPEN
                konnect_room.save(update_fields=["status", "updated_at"])
            except Exception:
                # non-fatal; continue to retry join
                pass

            # Retry join once after start
            try:
                join_resp_after_start = _external_join_as(user, "moderator")
            except Exception as exc:
                return Response({"detail": "Failed to call join service after start.", "error": str(exc)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

            if join_resp_after_start.get("data"):
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
                    pass

                return Response({"detail": "Room started and you have joined.", "link": join_resp_after_start["data"], "external_start": start_resp}, status=status.HTTP_200_OK)

            # If retry still failed, return both external responses for debugging
            return Response(
                {
                    "detail": "Unable to join even after starting the room.",
                    "join_response_before_start": join_resp,
                    "start_response": start_resp,
                    "join_response_after_start": join_resp_after_start
                },
                status=status.HTTP_502_BAD_GATEWAY
            )
        else:
            # Not creator — do not attempt to start; tell them room has not started
            return Response(
                {"detail": "Room has not started. Please confirm the moderator (room creator) has started the meeting."},
                status=status.HTTP_409_CONFLICT
            )

    # If join failed for another reason, surface that response
    return Response({"detail": "Cannot join meeting.", "external": join_resp}, status=status.HTTP_502_BAD_GATEWAY)

@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def list_rooms(request):
    """
    Returns all created rooms.
    Supports filters:
      ?status=open
      ?creator=me
    """

    user = request.user

    rooms = (
        KonnectRoom.objects
        .select_related("creator")
        .prefetch_related("allowed_courses", "allowed_users")
        .order_by("-created_at")
    )

    # Optional filters
    status_filter = request.GET.get("status")
    creator_filter = request.GET.get("creator")

    if status_filter:
        rooms = rooms.filter(status=status_filter)

    #if creator_filter == "me":
    rooms = rooms.filter(creator=user)

    serializer = KonnectRoomListSerializer(rooms, many=True)

    return Response(
        {
            "count": len(serializer.data),
            "results": serializer.data
        },
        status=status.HTTP_200_OK
    )





@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def student_available_rooms(request):
    """
    Returns all KonnectRoom rows the requesting student can join regardless of room status.
    Matching rules:
      - KonnectRoom.allowed_users contains the user
      OR
      - KonnectRoom.allowed_courses intersects with the student's active enrollments (Enrollment.Status.ACTIVE
        and completed_at IS NULL).
    Response:
      {
        "count": N,
        "results": [
           { "id": 12, "name": "...", "room_id": "...", "room_url": "...",
             "status": "open", "creator_name": "John Doe",
             "allowed_courses_count": 3, "allowed_users_count": 18,
             "created_at": "...", "updated_at": "..."
           },
           ...
        ]
      }
    """
    user = request.user

    # Try to resolve student profile (if present). If none, we still return rooms where user is explicitly allowed.
    student_profile = getattr(user, "student_profile", None)

    student_course_ids = []
    if student_profile:
        try:
            student_course_ids = list(
                Enrollment.objects.filter(
                    student=student_profile,
                    status=Enrollment.Status.ACTIVE,
                    completed_at__isnull=True
                ).values_list("course_id", flat=True)
            )
        except Exception:
            student_course_ids = []

    # Build query:
    # - allowed_users contains the user OR allowed_courses intersects student's active courses
    q = Q(allowed_users__pk=user.pk)
    if student_course_ids:
        q |= Q(allowed_courses__pk__in=student_course_ids)

    rooms_qs = (
        KonnectRoom.objects
        .filter(q)
        .annotate(
            allowed_courses_count=Count("allowed_courses", distinct=True),
            allowed_users_count=Count("allowed_users", distinct=True),
        )
        .select_related("creator")
        .order_by("-created_at")
        .distinct()
    )

    results = []
    for r in rooms_qs:
        results.append({
            "id": r.id,
            "name": r.name,
            "room_id": r.room_id,
            "room_url": getattr(r, "room_url", None) or None,
            "status": r.status,
            "creator_name": f"{r.creator.first_name} {r.creator.last_name}" if getattr(r, "creator", None) else None,
            "allowed_courses_count": getattr(r, "allowed_courses_count", 0),
            "allowed_users_count": getattr(r, "allowed_users_count", 0),
            "created_at": r.created_at.isoformat() if getattr(r, "created_at", None) else None,
            "updated_at": r.updated_at.isoformat() if getattr(r, "updated_at", None) else None,
        })

    return Response({
        "count": len(results),
        "results": results
    }, status=status.HTTP_200_OK)



