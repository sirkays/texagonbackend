# ===== Standard Library Imports =====
import logging
import math
from rest_framework.permissions import IsAuthenticated
import traceback
from collections import defaultdict
from datetime import datetime, timezone as py_tz
from decimal import Decimal
from typing import Any, DefaultDict, Dict, List, Optional
# ===== Django Imports =====
from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import (
    Avg,
    Count,
    DecimalField,
    F,
    IntegerField,
    OuterRef,
    Q,
    Subquery,
    Sum,
    Value,
    FloatField,
    Exists
)

from django.db.models.functions import Coalesce, Extract
from django.db.utils import DataError
from django.utils import dateparse, timezone
from django.utils.dateparse import parse_datetime

# ===== Third-Party Imports =====
from rest_framework import status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.response import Response
from rest_framework_api_key.permissions import HasAPIKey

# ===== Local App Imports =====
from api.models import SessionToken
from academics.models import StudentProfile, TeacherProfile
from assessments.models import Test, Question, Choice, TestAttempt, TestAnswer
from learning.models import Course, Enrollment, Lesson, Module
from orgs.models import Organization, OrganizationMembership
from core.utils import (
    _get_student_for_user,
    _is_org_admin_or_teacher,
    _resolve_org,
    get_object_or_404_ajax,
    get_or_make_device_id, user_agent, client_ip, hash_ip, COOKIE_NAME
)
from api.authentication import SessionTokenAuthentication
from .serializers import TestAttemptSerializer
from core.models import StudentDevice


logger = logging.getLogger(__name__)

def _has_field(model, field: str) -> bool:
    try:
        model._meta.get_field(field)
        return True
    except Exception:
        return False


def _difficulty_from_test(t: Test, q_count: int) -> str:
    if _has_field(Test, "difficulty") and getattr(t, "difficulty", None):
        return str(t.difficulty).title()
    dur = int(getattr(t, "duration_minutes", 0) or 0)
    if q_count >= 40 or dur >= 90:
        return "Advanced"
    if q_count <= 15 and dur <= 30:
        return "Beginner"
    return "Intermediate"


def _type_from_test(t: Test, q_count: int) -> str:
    if _has_field(Test, "type") and getattr(t, "type", None):
        return str(t.type).lower()
    if _has_field(Test, "is_exam") and getattr(t, "is_exam", False):
        return "exam"
    title = (getattr(t, "title", "") or "").lower()
    dur = int(getattr(t, "duration_minutes", 0) or 0)
    if "exam" in title or q_count >= 40 or dur >= 90:
        return "exam"
    return "quiz"


def _requires_subscription(t: Test) -> bool:
    if _has_field(Test, "requires_subscription"):
        return bool(getattr(t, "requires_subscription"))
    if _has_field(Test, "visibility"):
        vis = str(getattr(t, "visibility") or "").lower()
        return vis in {"premium", "paid", "subscriber_only"}
    return False


def _label_duration(t: Test) -> Optional[str]:
    if _has_field(Test, "duration_minutes") and getattr(t, "duration_minutes", None):
        return f"{int(t.duration_minutes)} mins"
    return None


def _question_text(q: Question) -> str:
    # Try common field names; fall back to str(q)
    for field in ("body", "text", "question", "title"):
        if _has_field(Question, field):
            val = getattr(q, field, None)
            if val:
                return str(val)
    return str(q)


def _question_order(q: Question) -> int:
    if _has_field(Question, "order"):
        try:
            return int(getattr(q, "order") or 0)
        except Exception:
            return 0
    return 0


def _choice_order(c: Choice) -> int:
    if _has_field(Choice, "order"):
        try:
            return int(getattr(c, "order") or 0)
        except Exception:
            return 0
    return 0


def _map_qtype(q: Question, choice_count: int, choice_texts: List[str]) -> str:
    """Normalize to: single-choice | true-false | short-answer | essay"""
    if _has_field(Question, "qtype") and getattr(q, "qtype", None):
        raw = str(getattr(q, "qtype")).lower().replace("_", "-")
        mapping = {
            "mcq": "multiple-choice",
            "multiple-choice": "multiple-choice",
            "multiple_choice": "multiple-choice",
            "scq":"single-choice",
            "single-choice": "single-choice",
            "single_choice": "single-choice",
            "tf": "true-false",
            "true-false": "true-false",
            "true_false": "true-false",
            "short": "short-answer",
            "short-answer": "short-answer",
            "short_answer": "short-answer",
            "essay": "essay",
            "long-answer": "essay",
            "long_answer": "essay",
        }
        return mapping.get(raw, raw)
    # Derive if no field:
    if choice_count == 2:
        low = [t.strip().lower() for t in choice_texts]
        if set(low) == {"true", "false"}:
            return "true-false"
    if choice_count >= 2:
        return "single-choice"
    # No choices provided:
    return "short-answer"





@api_view(["GET"])
@permission_classes([HasAPIKey])  # requires: Authorization: Api-Key <your_api_key>
@authentication_classes([SessionTokenAuthentication])  # requires: Authorization: SessionToken <session_token>
def my_test_attempts(request):
    """
    Fetch TestAttempt rows for the authenticated student.

    Optional query params:
      - test_id: int
      - status: str (e.g. in_progress|submitted|graded)
      - active=true|false  # if true, only attempts whose Test is currently open:
                           # (start_at <= now or null) AND (end_at >= now or null)
      - page: int (default 1)
      - page_size: int (default 20, max 100)
    """
    try:
        # Must be a student
        student = _get_student_for_user(request.user)
        if not student:
            return Response(
                {"detail": "Only students can access their test attempts."},
                status=status.HTTP_403_FORBIDDEN
            )

        qs = TestAttempt.objects.select_related("test").filter(student=student)
        # Filters
        test_id = request.query_params.get("test_id")
        if test_id:
            qs = qs.filter(test_id=test_id)

        status_param = request.query_params.get("status")
        if status_param:
            qs = qs.filter(status=status_param)
        # Active window filter (uses test.start_at/end_at)
        if request.query_params.get("active", "").lower() == "true":
            now = timezone.now()
            qs = qs.filter(
                (Q(test__start_at__isnull=True) | Q(test__start_at__lte=now)) &
                (Q(test__end_at__isnull=True) | Q(test__end_at__gte=now))
            )
        qs = qs.order_by("-created_at")

        # Simple pagination
        try:
            page = max(int(request.query_params.get("page", 1)), 1)
        except ValueError:
            page = 1

        try:
            page_size = min(max(int(request.query_params.get("page_size", 20)), 1), 100)
        except ValueError:
            page_size = 20

        start = (page - 1) * page_size
        end = start + page_size
        total = qs.count()

        serializer = TestAttemptSerializer(qs[start:end], many=True)
        return Response(
            {
                "count": total,
                "page": page,
                "page_size": page_size,
                "results": serializer.data,
            },
            status=status.HTTP_200_OK,
        )

    except Exception as e:
        # Log error (optional, if you have logging set up)
        # logger.exception("Error fetching test attempts")
        print(e)
        return Response(
            {"detail": f"An unexpected error occurred: {str(e)}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def available_tests_old(request):
    """
    List tests available to the current student (based on their enrollments).

    Query params:
      - include_past: '1' to include tests whose start_at is in the past (default excludes past-start)
      - course: course id to filter
      - debug: '1' to include traceback in error responses (dev only)
    """
    try:
        user = request.user
        student = _get_student_for_user(user)
        if not student:
            return Response({"tests": [], "detail": "No student profile found."}, status=status.HTTP_200_OK)

        enrollments = (Enrollment.objects
                       .filter(student=student)
                       .select_related("course")
                       .only("id", "course_id"))
        course_ids = list(enrollments.values_list("course_id", flat=True))
        if not course_ids:
            return Response({"tests": []}, status=status.HTTP_200_OK)

        qs = Test.objects.filter(course_id__in=course_ids)

        # Optional: filter by course
        course_filter = request.query_params.get("course")
        if course_filter:
            try:
                qs = qs.filter(course_id=int(course_filter))
            except ValueError:
                pass

        # Optional: exclude past-start tests if scheduled
        include_past = request.query_params.get("include_past") in {"1", "true", "True"}
        if _has_field(Test, "start_at") and not include_past:
            now = timezone.now()
            qs = qs.filter(Q(start_at__isnull=True) | Q(start_at__gte=now))

        # ---- SAFE question counts (no fragile reverse name) ----
        test_ids = list(qs.values_list("id", flat=True))
        q_counts = (
            Question.objects
            .filter(test_id__in=test_ids)
            .values("test_id")
            .annotate(c=Count("id"))
        )
        count_map = {row["test_id"]: int(row["c"] or 0) for row in q_counts}

        # Sorting
        if _has_field(Test, "start_at"):
            qs = qs.order_by("start_at", "-id")
        else:
            qs = qs.order_by("-id")

        items: List[Dict[str, Any]] = []
        for t in qs.select_related("course"):
            q_count = count_map.get(t.id, 0)

            # Human id for the UI
            if _has_field(Test, "slug") and getattr(t, "slug", None):
                id_str = t.slug
            else:
                id_str = f"test-{t.pk}"

            description = None
            if _has_field(Test, "description"):
                description = getattr(t, "description", None)
            if not description:
                cname = getattr(getattr(t, "course", None), "name", None)
                description = f"Assessment for {cname}" if cname else "Assessment"

            items.append({
                "id": id_str,
                "pk": t.pk,  # raw pk for later submit
                "title": getattr(t, "title", f"Test #{t.pk}"),
                "questions": q_count,
                "duration": _label_duration(t),
                "difficulty": _difficulty_from_test(t, q_count),
                "description": description,
                "type": _type_from_test(t, q_count),                 # "quiz" | "exam"
                "requiresSubscription": _requires_subscription(t),   # boolean
                "course": getattr(getattr(t, "course", None), "name", None),
                "startsAt": getattr(t, "start_at", None).isoformat() if _has_field(Test, "start_at") and getattr(t, "start_at", None) else None,
                "endsAt": getattr(t, "end_at", None).isoformat() if _has_field(Test, "end_at") and getattr(t, "end_at", None) else None,
            })

        return Response({"tests": items}, status=status.HTTP_200_OK)

    except Exception as e:
        # Return a helpful error payload; include traceback if debug flag or DEBUG=True
        payload = {
            "detail": "Error while fetching available tests.",
            "error": f"{type(e).__name__}: {e}",
        }
        if request.query_params.get("debug") in {"1", "true", "True"} or getattr(settings, "DEBUG", False):
            payload["traceback"] = traceback.format_exc()
        return Response(payload, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["POST"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def cbt_test_quit(request):
    if not getattr(request.user, "is_authenticated", False):
        return Response({"detail": "Authentication required."}, status=status.HTTP_401_UNAUTHORIZED)

    user = request.user
    student = _get_student_for_user(user)
    if not student:
        return Response({"detail": "Student profile not found for user."}, status=status.HTTP_400_BAD_REQUEST)

    payload = request.data or {}

    try:
        test = Test.objects.get(pk=payload.get('test_id'))
    except Test.DoesNotExist:
        return Response({"detail": "Test not found."}, status=status.HTTP_404_NOT_FOUND)

    submitted_at = timezone.now()
    TestAttempt.objects.get_or_create(
       test=test,
       student=student,
       defaults={
        "submitted_at":submitted_at,
        "score":0.0,
        "status":"quit"
       }
    )

    return Response({"status": "success"}, status=status.HTTP_200_OK)

    


@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def available_tests(request):
    try:
        user = request.user
        student = _get_student_for_user(user)
        if not student:
            resp = Response({"tests": [], "detail": "No student profile found."}, status=status.HTTP_200_OK)
            # Set cookie so the browser keeps a stable device id even if no student yet
            dev_id = get_or_make_device_id(request)
            resp.set_cookie(
                COOKIE_NAME,
                dev_id,
                httponly=True,
                samesite="None",   # ✅ REQUIRED for cross-domain
                secure=True,       # ✅ REQUIRED with SameSite=None
                max_age=60*60*24*365
            )

            return resp

        # ---- Device gate (FIRST device wins) ----
        dev_id = get_or_make_device_id(request)
        ua = user_agent(request)
        ip_h = hash_ip(client_ip(request))

        # Has the student registered a primary device yet?
        existing = StudentDevice.objects.filter(student=student).order_by("first_seen")
        if existing.exists():
            primary = existing.first()
            is_allowed_device = (primary.device_id == dev_id)
            if is_allowed_device:
                # touch last_seen for analytics
                StudentDevice.objects.filter(pk=primary.pk).update(last_seen=timezone.now())
            else:
                # Not the same device → return empty tests
                resp = Response({"tests": []}, status=status.HTTP_200_OK)
                # Still set/refresh cookie so this second device stays consistent
                resp.set_cookie(
                    COOKIE_NAME, dev_id, httponly=True, samesite="Lax",
                    secure=not settings.DEBUG, max_age=60*60*24*365
                )
                return resp
        else:
            # No device yet → register THIS device as the student's primary
            StudentDevice.objects.create(
                student=student, device_id=dev_id, user_agent=ua, ip_hash=ip_h
            )
            is_allowed_device = True
        # -----------------------------------------

        # From here on your original logic is unchanged
        enrollments = (Enrollment.objects
                       .filter(student=student)
                       .only("id", "course_id"))
        course_ids = list(enrollments.values_list("course_id", flat=True))
        if not course_ids:
            resp = Response({"tests": []}, status=status.HTTP_200_OK)
            resp.set_cookie(COOKIE_NAME, dev_id, httponly=True, samesite="Lax",
                            secure=not settings.DEBUG, max_age=60*60*24*365)
            return resp

        qs = Test.objects.filter(course_id__in=course_ids, start_at__isnull=False, end_at__isnull=False)
        course_filter = request.query_params.get("course")
        if course_filter:
            try:
                qs = qs.filter(course_id=int(course_filter))
            except ValueError:
                pass
        include_past = request.query_params.get("include_past") in {"1", "true", "True"}
        if _has_field(Test, "start_at") and not include_past:
            now = timezone.now()
            qs = qs.filter(
                (Q(start_at__isnull=True) | Q(start_at__lte=now)) &
                (Q(end_at__isnull=True) | Q(end_at__gte=now))
            )
        qs = qs.annotate(qcount=Count("questions", distinct=True)).filter(qcount__gt=0)
        student_attempts = TestAttempt.objects.filter(test_id=OuterRef("pk"), student=student)
        qs = qs.annotate(has_attempt=Exists(student_attempts)).filter(has_attempt=False)
        tests = list(qs.select_related("course"))
        test_ids = [t.id for t in tests]
        if not test_ids:
            resp = Response({"tests": []}, status=status.HTTP_200_OK)
            resp.set_cookie(COOKIE_NAME, dev_id, httponly=True, samesite="Lax",
                            secure=not settings.DEBUG, max_age=60*60*24*365)
            return resp

        questions = list(Question.objects.filter(test_id__in=test_ids))
        questions_by_test = defaultdict(list)
        for q in questions:
            questions_by_test[q.test_id].append(q)

        choice_qids = [q.id for q in questions]
        choices_map = defaultdict(list)
        if choice_qids:
            for c in Choice.objects.filter(question_id__in=choice_qids):
                choices_map[c.question_id].append(c)

        for tid in questions_by_test:
            questions_by_test[tid].sort(key=lambda q: (_question_order(q), q.id))
        for qid in choices_map:
            choices_map[qid].sort(key=lambda c: (_choice_order(c), c.id))

        include_answers = request.query_params.get("include_answers") in {"1", "true", "True"}

        if _has_field(Test, "start_at"):
            far_future = datetime.max.replace(tzinfo=py_tz.utc)
            tests.sort(key=lambda t: (getattr(t, "start_at", None) or far_future, -t.id))
        else:
            tests.sort(key=lambda t: -t.id)

        items = []
        for t in tests:
            test_qs = questions_by_test.get(t.id, [])
            q_count = len(test_qs)

            if _has_field(Test, "slug") and getattr(t, "slug", None):
                id_str = t.slug
            else:
                id_str = f"test-{t.pk}"

            description = None
            if _has_field(Test, "description"):
                description = getattr(t, "description", None)
            if not description:
                cname = getattr(getattr(t, "course", None), "name", None)
                description = f"Assessment for {cname}" if cname else "Assessment"

            questions_out = []
            for q in test_qs:
                q_choices = choices_map.get(q.id, [])
                choice_objs = [{"id": c.id, "text": getattr(c, "text", str(c))} for c in q_choices]
                choice_texts = [co["text"] for co in choice_objs]
                qtype_norm = _map_qtype(q, len(q_choices), choice_texts)
                q_out = {
                    "id": q.id,
                    "type": qtype_norm,
                    "question": _question_text(q),
                    "points": int(getattr(q, "points", 0) or 0),
                    "choices": choice_objs,
                    "options": choice_texts,
                }
                if include_answers and _has_field(Choice, "is_correct"):
                    correct_ids = [c.id for c in q_choices if getattr(c, "is_correct", False)]
                    if correct_ids:
                        q_out["correct_choice_ids"] = correct_ids
                        try:
                            first_id = correct_ids[0]
                            idx = next((i for i, co in enumerate(choice_objs) if co["id"] == first_id), None)
                            if idx is not None:
                                q_out["correct_index"] = idx
                        except Exception:
                            pass
                questions_out.append(q_out)

            items.append({
                "id": id_str,
                "pk": t.pk,
                "title": getattr(t, "title", f"Test #{t.pk}"),
                "questions": q_count,
                "duration": _label_duration(t),
                "difficulty": _difficulty_from_test(t, q_count),
                "description": description,
                "type": _type_from_test(t, q_count),
                "requiresSubscription": _requires_subscription(t),
                "course": getattr(getattr(t, "course", None), "name", None),
                "startsAt": getattr(t, "start_at", None).isoformat() if _has_field(Test, "start_at") and getattr(t, "start_at", None) else None,
                "endsAt": getattr(t, "end_at", None).isoformat() if _has_field(Test, "end_at") and getattr(t, "end_at", None) else None,
                "items": questions_out,
            })
        resp = Response({"tests": items}, status=status.HTTP_200_OK)
        # Ensure browser retains the same device id
        resp.set_cookie(
            COOKIE_NAME, dev_id, httponly=True, samesite="Lax",
            secure=not settings.DEBUG, max_age=60*60*24*365
        )
        return resp

    except Exception as e:
        payload = {"detail": "Error while fetching available tests.", "error": f"{type(e).__name__}: {e}"}
        if request.query_params.get("debug") in {"1", "true", "True"} or getattr(settings, "DEBUG", False):
            import traceback
            payload["traceback"] = traceback.format_exc()
        return Response(payload, status=status.HTTP_500_INTERNAL_SERVER_ERROR)



def _test_has_field(model, field_name: str) -> bool:
    try:
        model._meta.get_field(field_name)
        return True
    except Exception:
        return False



@api_view(["POST"])
@authentication_classes([SessionTokenAuthentication])
@permission_classes([HasAPIKey, IsAuthenticated])  # << add IsAuthenticated
@transaction.atomic
def submit_test(request, test_id: int):
    """
    Submit a completed test with all answers.

    Expected JSON body (examples):

    - SCQ/TF (single choice):
      {"answers":[{"question":101,"choice":555}, ...]}

    - MCQ (multiple choices):
      {"answers":[{"question":102,"choices":[561,562]}, ...]}

    - Short/Essay (free text):
      {"answers":[{"question":103,"text":"your answer"}, ...]}

    Optional meta:
      "started_at": "2025-08-23T12:00:00Z",
      "duration_seconds": 1760,
      "suspicious_activity": 2
    """
    try:
        # Extra safety, though IsAuthenticated already enforced
        if not getattr(request.user, "is_authenticated", False):
            return Response(
                {"detail": "Authentication required."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        user = request.user
        student = _get_student_for_user(user)
        if not student:
            return Response(
                {"detail": "Student profile not found for user."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            test = Test.objects.get(pk=test_id)
        except Test.DoesNotExist:
            return Response(
                {"detail": "Test not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        payload: Dict[str, Any] = request.data or {}

        raw_answers = payload.get("answers", [])
        # Allow no answers, but enforce that if provided, it must be a list
        if not isinstance(raw_answers, list):
            return Response(
                {"detail": "answers must be a list."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        answers_in: List[Dict[str, Any]] = raw_answers

        started_at_iso = payload.get("started_at")
        duration_seconds = payload.get("duration_seconds")  # currently unused
        suspicious_activity = payload.get("suspicious_activity")  # currently unused

        # Normalize started_at to a timezone-aware datetime for idempotency
        now = timezone.now()
        if started_at_iso:
            dt = parse_datetime(started_at_iso)
            if dt is None:
                started_at = now
            else:
                if timezone.is_naive(dt):
                    started_at = timezone.make_aware(dt, timezone.get_current_timezone())
                else:
                    started_at = dt
        else:
            started_at = now

        # Membership check: accept only questions that belong to this test
        test_question_ids = set(
            Question.objects.filter(test=test).values_list("id", flat=True)
        )

        # Total points from Questions
        total_points = (
            Question.objects.filter(test=test).aggregate(total=Sum("points"))["total"]
            or Decimal(0)
        )

        # Preload all correct choice ids per question
        correct_map: Dict[int, set[int]] = {
            qid: set(
                Choice.objects.filter(question_id=qid, is_correct=True).values_list(
                    "id", flat=True
                )
            )
            for qid in test_question_ids
        }

        # Preload points per question
        points_map: Dict[int, Decimal] = dict(
            Question.objects.filter(test=test).values_list("id", "points")
        )

        score = Decimal(0)
        auto_graded_count = 0
        pending_manual = 0
        breakdown: List[Dict[str, Any]] = []
        normalized_answers: List[Dict[str, Any]] = []
        answer_rows: List[TestAnswer] = []

        # ------------- GRADING LOOP -------------
        for item in answers_in:
            qid = item.get("question")
            if qid not in test_question_ids:
                logger.info(
                    "Ignoring answer for question not in test: question=%s test=%s",
                    qid,
                    test_id,
                )
                continue

            q_points = Decimal(points_map.get(qid, 0) or 0)
            awarded = Decimal(0)
            auto_graded = False

            selected_choice_id = None
            selected_choice_ids: List[int] = []
            answer_text = ""

            # ----- SCQ / TRUE-FALSE -----
            if "choice" in item and item["choice"] is not None:
                try:
                    selected_choice_id = int(item["choice"])
                except (ValueError, TypeError):
                    selected_choice_id = None

                auto_graded = True
                if selected_choice_id in correct_map.get(qid, set()):
                    awarded = q_points

            # ----- MCQ -----
            elif "choices" in item and isinstance(item["choices"], list):
                try:
                    selected_choice_ids = [int(x) for x in item["choices"]]
                except Exception:
                    selected_choice_ids = []
                auto_graded = True
                # all-or-nothing
                if set(selected_choice_ids) == correct_map.get(qid, set()):
                    awarded = q_points

            # ----- SHORT / ESSAY -----
            elif "text" in item:
                answer_text = (item.get("text") or "").strip()
                try:
                    q = Question.objects.only("id", "meta").get(id=qid)
                    correct_text = (q.meta or {}).get("correct_text")
                except Question.DoesNotExist:
                    correct_text = None
                except Exception:
                    correct_text = None

                if correct_text:
                    auto_graded = True
                    if str(correct_text).lower() in answer_text.lower():
                        awarded = q_points
                else:
                    # needs manual grading
                    pending_manual += 1

            else:
                logger.info(
                    "No recognizable answer format for question id=%s item=%s", qid, item
                )

            score += awarded
            if auto_graded:
                auto_graded_count += 1

            normalized_answers.append(
                {
                    "question": qid,
                    "choice": selected_choice_id,
                    "choices": selected_choice_ids,
                    "text": answer_text,
                    "awarded": float(awarded),
                    "points": float(q_points),
                    "auto_graded": auto_graded,
                }
            )

            breakdown.append(
                {
                    "question": qid,
                    "points": float(q_points),
                    "awarded": float(awarded),
                    "auto_graded": auto_graded,
                }
            )

            # Prepare TestAnswer row (FKs set via *_id)
            ans = TestAnswer(
                attempt=None,  # set later
                question_id=qid,
                selected_choice_id=selected_choice_id,
                selected_choice_ids=selected_choice_ids,
                answer_text=answer_text,
                awarded_points=awarded,
                is_auto_graded=auto_graded,
            )
            answer_rows.append(ans)

        answered = len(answer_rows)
        percentage = (
            int(round((float(score) / float(total_points)) * 100))
            if total_points
            else 0
        )

        # Optional: read pass_mark from Test.settings if present
        pass_mark = (
            test.settings.get("pass_mark")
            if isinstance(getattr(test, "settings", {}), dict)
            and "pass_mark" in test.settings
            else 50
        )
        try:
            pass_mark = int(pass_mark)
        except (ValueError, TypeError):
            pass_mark = 45

        result = "PASS" if percentage >= pass_mark else "FAIL"

        # ------------- ATTEMPT HANDLING (IDEMPOTENT) -------------
        # First: does this exact "run" (same started_at) already exist & submitted?
        existing_same_run = TestAttempt.objects.filter(
            test=test, student=student, started_at=started_at
        ).first()

        if existing_same_run and existing_same_run.status in ("submitted", "graded"):
            # 🔁 This is an idempotent retry of an already-submitted run
            existing_score = existing_same_run.score or Decimal(0)
            existing_percentage = (
                int(
                    round(
                        (float(existing_score) / float(total_points)) * 100
                    )
                )
                if total_points
                else 0
            )
            existing_result = (
                "PASS" if existing_percentage >= pass_mark else "FAIL"
            )

            return Response(
                {
                    "attempt_id": existing_same_run.id,
                    "score": float(existing_score),
                    "total_points": float(total_points),
                    "percentage": existing_percentage,
                    "result": existing_result,
                    "answered": existing_same_run.answers_rows.count(),
                    "auto_graded": auto_graded_count,  # from this grading pass
                    "pending_manual": pending_manual,
                    "breakdown": breakdown,
                },
                status=status.HTTP_200_OK,
            )

        # Second: if there is another submitted attempt for this test/student (different started_at),
        # we treat it as a new attempt and block it.
        other_submitted = (
            TestAttempt.objects.filter(
                test=test,
                student=student,
                status__in=["submitted", "graded"],
            )
            .exclude(pk=getattr(existing_same_run, "pk", None))
            .first()
        )

        if other_submitted:
            return Response(
                {"detail": "User already performed this test."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Now we either:
        # - update an existing in-progress / quit attempt for this run, or
        # - create a new attempt
        try:
            if existing_same_run and existing_same_run.status in (
                "in_progress",
                "quit",
            ):
                attempt = existing_same_run
                attempt.submitted_at = now
                attempt.score = score
                attempt.answers = normalized_answers
                attempt.status = "submitted"
                attempt.save()

                # Replace previous answer rows with new ones
                attempt.answers_rows.all().delete()
                for a in answer_rows:
                    a.attempt = attempt
                TestAnswer.objects.bulk_create(answer_rows, ignore_conflicts=True)

            else:
                attempt = TestAttempt.objects.create(
                    test=test,
                    student=student,
                    started_at=started_at,
                    submitted_at=now,
                    score=score,
                    answers=normalized_answers,
                    status="submitted",
                )
                for a in answer_rows:
                    a.attempt = attempt
                TestAnswer.objects.bulk_create(answer_rows, ignore_conflicts=True)

        except (IntegrityError, DataError):
            logger.exception("Failed creating or updating TestAttempt")

            # Try to recover by fetching an existing attempt for this run
            attempt = TestAttempt.objects.filter(
                test=test, student=student, started_at=started_at
            ).first()
            if not attempt:
                return Response(
                    {"detail": "Server error creating attempt."},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )
        print(            {
                "attempt_id": attempt.id,
                "score": float(score),
                "total_points": float(total_points),
                "percentage": percentage,
                "result": result,
                "answered": answered,
                "auto_graded": auto_graded_count,
                "pending_manual": pending_manual,
                "breakdown": breakdown,
            },)
        # ------------- FINAL RESPONSE -------------
        return Response(
            {
                "attempt_id": attempt.id,
                "score": float(score),
                "total_points": float(total_points),
                "percentage": percentage,
                "result": result,
                "answered": answered,
                "auto_graded": auto_graded_count,
                "pending_manual": pending_manual,
                "breakdown": breakdown,
            },
            status=status.HTTP_200_OK,
        )

    except Exception:
        logger.exception("Unexpected error in submit_test")
        return Response(
            {"detail": "Unexpected server error while submitting test."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )



def _get_teacher_for_user(user) -> Optional[TeacherProfile]:
    """Get teacher profile for the authenticated user."""
    mem = (OrganizationMembership.objects
           .filter(user=user, is_active=True, role__in=['teacher', 'admin', 'owner'])
           .select_related("organization")
           .order_by("-id")
           .first())
    if mem:
        tp = TeacherProfile.objects.filter(user=user, organization=mem.organization).first()
        if tp:
            return tp
    return TeacherProfile.objects.filter(user=user).order_by("-id").first()


def _serialize_question(question: Question, include_correct_answers: bool = True) -> Dict[str, Any]:
    """Serialize a question with its choices."""
    choices = list(question.choices.all().order_by('order', 'id'))
    
    # Map question types to frontend format
    qtype_mapping = {
        'scq': 'single-choice',
        'mcq': 'multiple-choice', 
        'tf': 'true-false',
        'true_false': 'true-false',
        'short': 'short-answer',
        'essay': 'essay'
    }
    
    qtype = getattr(question, 'qtype', 'scq')
    frontend_type = qtype_mapping.get(qtype, 'single-choice')
    
    # Get question text
    question_text = getattr(question, 'body', '') or getattr(question, 'text', '') or str(question)
    
    result = {
        'id': str(question.id),
        'type': frontend_type,
        'question': question_text,
        'points': int(getattr(question, 'points', 1) or 1),
        'options': [getattr(choice, 'text', str(choice)) for choice in choices],
        'explanation': getattr(question, 'explanation', '') or (question.meta or {}).get('explanation', ''),
        'difficulty': (question.meta or {}).get('difficulty', 'Medium')
    }
    
    if include_correct_answers and choices:
        if frontend_type == 'true-false':
            # For true/false, find the correct option
            correct_choice = next((choice for choice in choices if getattr(choice, 'is_correct', False)), None)
            if correct_choice:
                result['correctAnswer'] = getattr(correct_choice, 'text', '').lower() == 'true'
        elif frontend_type == 'single-choice':
            # For multiple choice, get index of correct answer
            correct_choices = [i for i, choice in enumerate(choices) if getattr(choice, 'is_correct', False)]
            if correct_choices:
                result['correctAnswer'] = correct_choices[0]  # Use first correct answer for single choice
        # For short-answer and essay, correctAnswer might be in meta
        elif frontend_type in ['short-answer', 'essay']:
            result['correctAnswer'] = (question.meta or {}).get('correct_answer', '')
    
    return result



def _safe_int(val, default=0):
    try:
        return int(val)
    except (TypeError, ValueError):
        return default



def _iso(value):
    """
    Return ISO8601 for datetimes, pass strings through unchanged, or None.
    This prevents 'str' object has no attribute 'utcoffset' errors.
    """
    if value in (None, ""):
        return None

    # If DB/model field holds a string, just return it
    if isinstance(value, str):
        return value

    # If it's a datetime, normalize to UTC and format
    if isinstance(value, datetime):
        if timezone.is_naive(value):
            value = timezone.make_aware(value, timezone=py_tz.utc)
        else:
            value = value.astimezone(py_tz.utc)
        return value.isoformat(timespec="seconds")

    # Fallback: stringify anything else
    return str(value)


def _to_aware_utc(value):
    """
    Accept str|datetime|None; return aware UTC datetime or None.
    Raises ValueError/TypeError for bad inputs.
    """
    if value in (None, ""):
        return None

    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        # Handles '...+00:00' and '...Z'
        dt = parse_datetime(value)
        if dt is None:
            raise ValueError(f"Invalid datetime format: {value!r}. Use ISO8601, e.g. 2025-09-24T20:48:26Z")
    else:
        raise TypeError(f"Unsupported type for datetime: {type(value).__name__}")

    # Normalize to aware UTC
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone=py_tz.utc)
    else:
        dt = dt.astimezone(py_tz.utc)
    return dt


def _serialize_test(test, include_questions: bool = False) -> Dict[str, Any]:
    """Serialize a test object safely (no NoneType.isoformat errors)."""
    # Questions info (guard relation existence)
    questions_manager = getattr(test, "questions", None)
    if questions_manager is not None:
        questions_count = questions_manager.count()
        agg = questions_manager.aggregate(total=Sum("points"))
        total_points = _safe_int(agg.get("total"), 0)
    else:
        questions_count = 0
        total_points = 0

    # Settings may be dict-like or None
    settings_dict = getattr(test, "settings", None) or {}
    difficulty = settings_dict.get("difficulty", "Medium")

    # Course/category
    course = getattr(test, "course", None)
    category = getattr(course, "name", "") if course else ""

    # Duration and totals
    duration_minutes = getattr(test, "duration_minutes", None)
    duration = _safe_int(duration_minutes if duration_minutes is not None else 30, 30)

    total_marks = getattr(test, "total_marks", None)
    try:
        total_marks = int(total_marks) if total_marks is not None else None
    except (TypeError, ValueError):
        total_marks = None

    # Visibility/published
    visibility = getattr(test, "visibility", "draft")
    is_published = (visibility == "published")
    result: Dict[str, Any] = {
        "id": str(getattr(test, "id", "")),
        "title": getattr(test, "title", f"Test #{getattr(test, 'id', '')}"),
        "instructions": getattr(test, "instructions", "") or "",
        "duration": duration,
        "total_marks": total_marks,
        "totalPoints": total_points,
        "difficulty": difficulty,
        "category": category,
        "isPublished": is_published,
        "questionsCount": questions_count,
        "course_id":course.id,
        # created/updated
        "createdAt": _iso(getattr(test, "created_at", None)),
        "updatedAt": _iso(getattr(test, "updated_at", None)),

        # windows (handle None safely)
        "start_at": _iso(getattr(test, "start_at", None)),
        "end_at": _iso(getattr(test, "end_at", None)),
    }
    if include_questions and questions_manager is not None:
        questions = list(questions_manager.all().order_by("order", "id"))
        result["questions"] = [_serialize_question(q) for q in questions]  # assumes this helper exists

    return result


@api_view(["PUT", "PATCH"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
@transaction.atomic
def update_test(request, test_id: int):
    """Update test details (title, instructions/description, duration, settings, start_at, end_at)."""
    try:
        user = request.user
        teacher = _get_teacher_for_user(user)
        if not teacher:
            return Response({"detail": "Teacher profile not found."}, status=status.HTTP_403_FORBIDDEN)

        try:
            test = Test.objects.select_for_update().get(id=test_id, course__teacher=teacher)
        except Test.DoesNotExist:
            return Response({"detail": "Test not found or access denied."}, status=status.HTTP_404_NOT_FOUND)

        data = request.data or {}
        # Core fields
        if "title" in data:
            test.title = (data["title"] or "").strip()

        # Allow either `instructions` or `description` (your client sends 'description')
        if "instructions" in data:
            test.instructions = data["instructions"] or ""
        elif "description" in data:
            test.instructions = data["description"] or ""

        if "total_marks" in data:
            try:
                test.total_marks = int(data["total_marks"]) if data["total_marks"] is not None else None
            except (TypeError, ValueError):
                return Response({"detail": "total_marks must be an integer or null."},
                                status=status.HTTP_400_BAD_REQUEST)

        if "duration" in data:
            try:
                test.duration_minutes = max(1, int(data["duration"]))
            except (TypeError, ValueError):
                return Response({"detail": "duration must be a positive integer (minutes)."},
                                status=status.HTTP_400_BAD_REQUEST)
        if "start_at" in data:
            test.start_at = _to_aware_utc(data["start_at"])
        if "end_at" in data:
            test.end_at = _to_aware_utc(data["end_at"])

        # Settings (difficulty/category)
        settings_dict = test.settings or {}
        if "difficulty" in data:
            settings_dict["difficulty"] = data["difficulty"]
        if "category" in data:
            settings_dict["category"] = data["category"]
        test.settings = settings_dict

        test.save()

        serialized_test = _serialize_test(test, include_questions=True)
        return Response(
            {"test": serialized_test, "message": "Test updated successfully."},
            status=status.HTTP_200_OK
        )

    except (ValueError, TypeError) as e:
        # Likely bad datetime or type issues
        payload = {"detail": "Invalid input.", "error": f"{type(e).__name__}: {e}"}
        if request.query_params.get("debug") in {"1", "true"} or getattr(settings, "DEBUG", False):
            payload["traceback"] = traceback.format_exc()
        return Response(payload, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        payload = {"detail": "Error updating test.", "error": f"{type(e).__name__}: {e}"}
        if request.query_params.get("debug") in {"1", "true"} or getattr(settings, "DEBUG", False):
            payload["traceback"] = traceback.format_exc()
        return Response(payload, status=status.HTTP_500_INTERNAL_SERVER_ERROR)



@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def teacher_tests_list(request):
    """
    List all tests created by the authenticated teacher.
    
    Query params:
      - course: filter by course ID
      - published: 'true'/'false' to filter by publication status
      - search: search in title and description
      - page: page number for pagination (default: 1)
      - limit: items per page (default: 20, max: 100)
    """
    try:
        user = request.user
        teacher = _get_teacher_for_user(user)
        if not teacher:
            return Response({"detail": "Teacher profile not found."}, status=status.HTTP_403_FORBIDDEN)

        # Get teacher's courses
        teacher_courses = Course.objects.filter(teacher=teacher)
        
        # Base queryset
        qs = Test.objects.filter(course__in=teacher_courses).select_related('course')
        
        # Apply filters
        course_filter = request.query_params.get('course')
        if course_filter:
            try:
                qs = qs.filter(course_id=int(course_filter))
            except ValueError:
                pass
        
        published_filter = request.query_params.get('published')
        if published_filter == 'true':
            qs = qs.filter(visibility='published')
        elif published_filter == 'false':
            qs = qs.exclude(visibility='published')
        
        search = request.query_params.get('search', '').strip()
        if search:
            qs = qs.filter(Q(title__icontains=search) | Q(instructions__icontains=search))
        
        # Pagination
        try:
            page = max(1, int(request.query_params.get('page', 1)))
            limit = min(100, max(1, int(request.query_params.get('limit', 20))))
        except ValueError:
            page, limit = 1, 20
        
        total_count = qs.count()
        offset = (page - 1) * limit
        tests = list(qs.order_by('-created_at')[offset:offset + limit])
        
        # Serialize tests
        serialized_tests = [_serialize_test(test) for test in tests]
        
        return Response({
            "tests": serialized_tests,
            "pagination": {
                "page": page,
                "limit": limit,
                "total": total_count,
                "pages": (total_count + limit - 1) // limit
            }
        }, status=status.HTTP_200_OK)
        
    except Exception as e:
        payload = {
            "detail": "Error fetching teacher tests.",
            "error": f"{type(e).__name__}: {e}",
        }
        if request.query_params.get("debug") in {"1", "true"} or getattr(settings, "DEBUG", False):
            payload["traceback"] = traceback.format_exc()
        return Response(payload, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def teacher_test_detail(request, test_id: int):
    """Get detailed information about a specific test including all questions."""
    try:
        user = request.user
        teacher = _get_teacher_for_user(user)
        if not teacher:
            return Response({"detail": "Teacher profile not found."}, status=status.HTTP_403_FORBIDDEN)

        try:
            test = Test.objects.select_related('course').get(
                id=test_id, 
                course__teacher=teacher
            )
        except Test.DoesNotExist:
            return Response({"detail": "Test not found or access denied."}, status=status.HTTP_404_NOT_FOUND)
        serialized_test = _serialize_test(test, include_questions=True)
        return Response({"test": serialized_test}, status=status.HTTP_200_OK)
        
    except Exception as e:
        payload = {
            "detail": "Error fetching test details.",
            "error": f"{type(e).__name__}: {e}",
        }
        if request.query_params.get("debug") in {"1", "true"} or getattr(settings, "DEBUG", False):
            payload["traceback"] = traceback.format_exc()
        return Response(payload, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["POST"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
@transaction.atomic
def create_test(request):
    """
    Create a new test.
    
    Expected JSON body:
    {
        "title": "Algebra Midterm",
        "instructions": "Complete all questions within the time limit.",
        "duration": 60,
        "difficulty": "Medium",
        "course_id": 123,
        "category": "Math",
        "start_date": "2025-09-25T09:00:00Z",
        "end_date": "2025-09-25T10:00:00Z"
    }

    """
    try:
        user = request.user
        teacher = _get_teacher_for_user(user)
        if not teacher:
            return Response({"detail": "Teacher profile not found."}, status=status.HTTP_403_FORBIDDEN)

        data = request.data or {}

        # Validate required fields
        title = data.get('title', '').strip()
        if not title:
            return Response({"detail": "Title is required."}, status=status.HTTP_400_BAD_REQUEST)
        
        course_id = data.get('course_id')
        if not course_id:
            return Response({"detail": "Course ID is required."}, status=status.HTTP_400_BAD_REQUEST)
        
        # Verify teacher owns the course
        try:
            course = Course.objects.get(id=course_id, teacher=teacher)
        except Course.DoesNotExist:
            return Response({"detail": "Course not found or access denied."}, status=status.HTTP_404_NOT_FOUND)

        if "start_at" in data:
            start_at = _to_aware_utc(data["start_at"])
        if "end_at" in data:
            end_at = _to_aware_utc(data["end_at"])     

        # Create test
        test_data = {
            'course': course,
            'title': title,
            'start_at':start_at,
            'end_at':end_at,
            'instructions': data.get('instructions', ''),
            'total_marks': data.get("total_marks", 100),
            'duration_minutes': max(1, int(data.get('duration', 30))),
            'visibility': 'draft',
            'settings': {
                'difficulty': data.get('difficulty', 'Medium'),
                'category': data.get('category', ''),
            }
        }
        
        test = Test.objects.create(**test_data)
        
        serialized_test = _serialize_test(test, include_questions=True)
        
        return Response({
            "test": serialized_test,
            "message": "Test created successfully."
        }, status=status.HTTP_201_CREATED)
        
    except Exception as e:
        payload = {
            "detail": "Error creating test.",
            "error": f"{type(e).__name__}: {e}",
        }
        if request.query_params.get("debug") in {"1", "true"} or getattr(settings, "DEBUG", False):
            payload["traceback"] = traceback.format_exc()
        return Response(payload, status=status.HTTP_500_INTERNAL_SERVER_ERROR)




@api_view(["DELETE"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
@transaction.atomic
def delete_test(request, test_id: int):
    """Delete a test and all its questions."""
    try:
        user = request.user
        teacher = _get_teacher_for_user(user)
        if not teacher:
            return Response({"detail": "Teacher profile not found."}, status=status.HTTP_403_FORBIDDEN)

        try:
            test = Test.objects.get(id=test_id, course__teacher=teacher)
        except Test.DoesNotExist:
            return Response({"detail": "Test not found or access denied."}, status=status.HTTP_404_NOT_FOUND)
        
        test.delete()
        
        return Response({
            "message": "Test deleted successfully."
        }, status=status.HTTP_200_OK)
        
    except Exception as e:
        payload = {
            "detail": "Error deleting test.",
            "error": f"{type(e).__name__}: {e}",
        }
        if request.query_params.get("debug") in {"1", "true"} or getattr(settings, "DEBUG", False):
            payload["traceback"] = traceback.format_exc()
        return Response(payload, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["POST"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
@transaction.atomic
def publish_test(request, test_id: int):
    """Publish or unpublish a test."""
    try:
        user = request.user
        teacher = _get_teacher_for_user(user)
        if not teacher:
            return Response({"detail": "Teacher profile not found."}, status=status.HTTP_403_FORBIDDEN)

        try:
            test = Test.objects.get(id=test_id, course__teacher=teacher)
        except Test.DoesNotExist:
            return Response({"detail": "Test not found or access denied."}, status=status.HTTP_404_NOT_FOUND)
        
        data = request.data or {}
        is_published = data.get('published', True)
        
        # Validate test has questions before publishing
        if is_published and not test.questions.exists():
            return Response({
                "detail": "Cannot publish test without questions."
            }, status=status.HTTP_400_BAD_REQUEST)
        
        test.visibility = 'published' if is_published else 'draft'
        test.save()
        
        serialized_test = _serialize_test(test)
        
        return Response({
            "test": serialized_test,
            "message": f"Test {'published' if is_published else 'unpublished'} successfully."
        }, status=status.HTTP_200_OK)
        
    except Exception as e:
        payload = {
            "detail": "Error publishing test.",
            "error": f"{type(e).__name__}: {e}",
        }
        if request.query_params.get("debug") in {"1", "true"} or getattr(settings, "DEBUG", False):
            payload["traceback"] = traceback.format_exc()
        return Response(payload, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["POST"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
@transaction.atomic
def duplicate_test(request, test_id: int):
    """Create a duplicate of an existing test."""
    try:
        user = request.user
        teacher = _get_teacher_for_user(user)
        if not teacher:
            return Response({"detail": "Teacher profile not found."}, status=status.HTTP_403_FORBIDDEN)

        try:
            original_test = Test.objects.get(id=test_id, course__teacher=teacher)
        except Test.DoesNotExist:
            return Response({"detail": "Test not found or access denied."}, status=status.HTTP_404_NOT_FOUND)
        
        # Create duplicate test
        duplicate_test = Test.objects.create(
            course=original_test.course,
            title=f"{original_test.title} (Copy)",
            instructions=original_test.instructions,
            duration_minutes=original_test.duration_minutes,
            visibility='draft',
            settings=original_test.settings or {}
        )
        
        # Duplicate questions and choices
        original_questions = original_test.questions.all().order_by('order', 'id')
        for question in original_questions:
            new_question = Question.objects.create(
                test=duplicate_test,
                order=question.order,
                qtype=question.qtype,
                body=question.body,
                points=question.points,
                meta=question.meta or {}
            )
            
            # Duplicate choices
            for choice in question.choices.all().order_by('order', 'id'):
                Choice.objects.create(
                    question=new_question,
                    order=choice.order,
                    text=choice.text,
                    is_correct=choice.is_correct
                )
        
        serialized_test = _serialize_test(duplicate_test, include_questions=True)
        
        return Response({
            "test": serialized_test,
            "message": "Test duplicated successfully."
        }, status=status.HTTP_201_CREATED)
        
    except Exception as e:
        payload = {
            "detail": "Error duplicating test.",
            "error": f"{type(e).__name__}: {e}",
        }
        if request.query_params.get("debug") in {"1", "true"} or getattr(settings, "DEBUG", False):
            payload["traceback"] = traceback.format_exc()
        return Response(payload, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["POST"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
@transaction.atomic
def add_question(request, test_id: int):
    """
    Add a new question to a test.
    
    Expected JSON body:
    {
        "type": "single-choice",
        "question": "What is 2+2?",
        "options": ["3", "4", "5", "6"],
        "correctAnswer": 1,
        "points": 2,
        "explanation": "Basic math",
        "difficulty": "Easy"
    }
    """
    try:
        user = request.user
        teacher = _get_teacher_for_user(user)
        if not teacher:
            return Response({"detail": "Teacher profile not found."}, status=status.HTTP_403_FORBIDDEN)

        try:
            test = Test.objects.get(id=test_id, course__teacher=teacher)
        except Test.DoesNotExist:
            return Response({"detail": "Test not found or access denied."}, status=status.HTTP_404_NOT_FOUND)
        
        data = request.data or {}
        
        # Validate required fields
        question_text = data.get('question', '').strip()
        if not question_text:
            return Response({"detail": "Question text is required."}, status=status.HTTP_400_BAD_REQUEST)
        
        question_type = data.get('type', 'single-choice')
        points = max(1, int(data.get('points', 1)))
        
        # Map frontend types to backend types
        type_mapping = {
            'single-choice': 'scq',
            'true-false': 'tf',
            'short-answer': 'short',
            'essay': 'essay'
        }
        qtype = type_mapping.get(question_type, 'scq')
        
        # Get next order
        max_order = test.questions.aggregate(max_order=Count('order'))['max_order'] or 0
        order = max_order + 1
        
        # Create question
        question = Question.objects.create(
            test=test,
            order=order,
            qtype=qtype,
            body=question_text,
            points=points,
            meta={
                'explanation': data.get('explanation', ''),
                'difficulty': data.get('difficulty', 'Medium')
            }
        )
        
        # Create choices if provided
        options = data.get('options', [])
        correct_answer = data.get('correctAnswer')
        
        if options and question_type in ['single-choice', 'true-false']:
            for i, option_text in enumerate(options):
                is_correct = False
                if question_type == 'true-false':
                    is_correct = (option_text.lower() == 'true' and correct_answer is True) or \
                                (option_text.lower() == 'false' and correct_answer is False)
                elif question_type == 'single-choice' and isinstance(correct_answer, int):
                    is_correct = (i == correct_answer)
                
                Choice.objects.create(
                    question=question,
                    order=i + 1,
                    text=option_text,
                    is_correct=is_correct
                )
        elif question_type in ['short-answer', 'essay'] and correct_answer:
            # Store correct answer in meta for text questions
            question.meta['correct_answer'] = correct_answer
            question.save()
        
        serialized_question = _serialize_question(question)
        
        return Response({
            "question": serialized_question,
            "message": "Question added successfully."
        }, status=status.HTTP_201_CREATED)
        
    except Exception as e:
        payload = {
            "detail": "Error adding question.",
            "error": f"{type(e).__name__}: {e}",
        }
        if request.query_params.get("debug") in {"1", "true"} or getattr(settings, "DEBUG", False):
            payload["traceback"] = traceback.format_exc()
        return Response(payload, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["PUT", "PATCH"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
@transaction.atomic
def update_question(request, test_id: int, question_id: int):
    """Update a question and its choices."""
    try:
        user = request.user
        teacher = _get_teacher_for_user(user)
        if not teacher:
            return Response({"detail": "Teacher profile not found."}, status=status.HTTP_403_FORBIDDEN)
        try:
            question = Question.objects.get(
                id=question_id,
                test__id=test_id,
                test__course__teacher=teacher
            )
        except Question.DoesNotExist:
            return Response({"detail": "Question not found or access denied."}, status=status.HTTP_404_NOT_FOUND)
        
        data = request.data or {}
        # Update question fields
        if 'question' in data:
            question.body = data['question'].strip()
        if 'points' in data:
            question.points = max(1, int(data['points']))
        
        # Update meta
        meta = question.meta or {}
        if 'explanation' in data:
            meta['explanation'] = data['explanation']
        if 'difficulty' in data:
            meta['difficulty'] = data['difficulty']
        question.meta = meta
        
        question.save()
        # Update choices if provided
        if 'options' in data:
            # Delete existing choices
            question.choices.all().delete()
            
            options = data.get('options', [])
            correct_answer = data.get('correctAnswer')
            question_type = data.get('type', 'single-choice')
            
            for i, option_text in enumerate(options):
                is_correct = False
                if question_type == 'true-false':
                    is_correct = (option_text.lower() == 'true' and correct_answer is True) or \
                                (option_text.lower() == 'false' and correct_answer is False)
                elif question_type == 'single-choice' and isinstance(correct_answer, int):
                    is_correct = (i == correct_answer)
                
                Choice.objects.create(
                    question=question,
                    order=i + 1,
                    text=option_text,
                    is_correct=is_correct
                )
        
        # Update correct answer for text questions
        if 'correctAnswer' in data and data.get('type') in ['short-answer', 'essay']:
            meta = question.meta or {}
            meta['correct_answer'] = data['correctAnswer']
            question.meta = meta
            question.save()
        serialized_question = _serialize_question(question)
        return Response({
            "question": serialized_question,
            "message": "Question updated successfully."
        }, status=status.HTTP_200_OK)
        
    except Exception as e:
        payload = {
            "detail": "Error updating question.",
            "error": f"{type(e).__name__}: {e}",
        }
        if request.query_params.get("debug") in {"1", "true"} or getattr(settings, "DEBUG", False):
            payload["traceback"] = traceback.format_exc()
        return Response(payload, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["DELETE"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
@transaction.atomic
def delete_question(request, test_id: int, question_id: int):
    """Delete a question from a test."""
    try:
        user = request.user
        teacher = _get_teacher_for_user(user)
        if not teacher:
            return Response({"detail": "Teacher profile not found."}, status=status.HTTP_403_FORBIDDEN)

        try:
            question = Question.objects.get(
                id=question_id,
                test_id=test_id,
                test__course__teacher=teacher
            )
        except Question.DoesNotExist:
            return Response({"detail": "Question not found or access denied."}, status=status.HTTP_404_NOT_FOUND)
        
        question.delete()
        
        return Response({
            "message": "Question deleted successfully."
        }, status=status.HTTP_200_OK)
        
    except Exception as e:
        payload = {
            "detail": "Error deleting question.",
            "error": f"{type(e).__name__}: {e}",
        }
        if request.query_params.get("debug") in {"1", "true"} or getattr(settings, "DEBUG", False):
            payload["traceback"] = traceback.format_exc()
        return Response(payload, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def teacher_courses(request):
    """Get list of courses taught by the authenticated teacher."""
    try:
        user = request.user
        teacher = _get_teacher_for_user(user)
        if not teacher:
            return Response({"detail": "Teacher profile not found."}, status=status.HTTP_403_FORBIDDEN)

        courses = Course.objects.filter(teacher=teacher, is_active=True).select_related('subject', 'classroom')
        
        courses_data = []
        for course in courses:
            courses_data.append({
                'id': course.id,
                'name': course.name,
                'subject': getattr(course.subject, 'name', '') if course.subject else '',
                'classroom': getattr(course.classroom, 'name', '') if course.classroom else '',
                'description': course.description
            })
        
        return Response({"courses": courses_data}, status=status.HTTP_200_OK)
        
    except Exception as e:
        payload = {
            "detail": "Error fetching teacher courses.",
            "error": f"{type(e).__name__}: {e}",
        }
        if request.query_params.get("debug") in {"1", "true"} or getattr(settings, "DEBUG", False):
            payload["traceback"] = traceback.format_exc()
        return Response(payload, status=status.HTTP_500_INTERNAL_SERVER_ERROR)



@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def student_performance_summary(request):
    """
    GET /api/teacher/student-performance/summary?test_id=<optional_test_id>

    Returns a summary of student performances, optionally filtered by test_id.

    Headers:
      Authorization: Api-Key <YOUR_API_KEY>
      X-Session-Token: <session_token>

    Query Params:
      test_id: Optional integer to filter performances by a specific test.
    """
    try:
        org, err = _resolve_org(request)
        if err:
            return err

        if not _is_org_admin_or_teacher(request, org):
            return Response({"detail": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

        # Determine if user is teacher and get profile
        teacher_profile = None
        try:
            teacher_profile = request.user.teacher_profile
            if teacher_profile.organization != org:
                teacher_profile = None
        except TeacherProfile.DoesNotExist:
            pass

        test_id = request.query_params.get("test_id")
        filters = Q(test__course__organization=org)
        if teacher_profile:
            filters &= Q(test__course__teacher=teacher_profile)
        if test_id:
            filters &= Q(test_id=test_id)

        # Get all relevant TestAttempts
        attempts = TestAttempt.objects.filter(filters).select_related(
            "test", "test__course", "student", "student__user", "student__current_classroom"
        )

        # Aggregates
        total_attempts = attempts.count()
        avg_score = attempts.aggregate(avg=Coalesce(Avg("score"), Decimal("0"), output_field=DecimalField())).get("avg", Decimal("0"))
        pass_rate = attempts.filter(score__gte=F("test__total_marks") * Decimal("0.7")).count() / max(1, total_attempts) * 100 if total_attempts else 0
        avg_completion_time = attempts.filter(submitted_at__isnull=False).aggregate(
            avg=Coalesce(Avg(Extract(F("submitted_at") - F("started_at"), 'epoch') / 60), Value(0.0))
        ).get("avg", 0)

        payload = {
            "totalAttempts": total_attempts,
            "averageScore": float(avg_score),
            "passRate": round(pass_rate, 1),
            "averageCompletionTime": round(avg_completion_time),
        }
        return Response(payload)

    except Exception as e:
        traceback.print_exc()
        return Response({"detail": "Unexpected error", "error": str(e)},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def student_performance_list(request):
    """
    GET /api/teacher/student-performance/list?test_id=<optional>&student_filter=<str>&sort_field=<score|completionTime|submittedAt>&sort_order=<asc|desc>&page=<int>&limit=<int>

    Returns paginated list of student performances for the table.

    Headers:
      Authorization: Api-Key <YOUR_API_KEY>
      X-Session-Token: <session_token>

    Query Params:
      test_id: Optional integer to filter by test.
      student_filter: Optional string to filter by student name (ilike).
      sort_field: Optional, one of 'score', 'completionTime', 'submittedAt' (default 'score').
      sort_order: Optional, 'asc' or 'desc' (default 'desc').
      page: Optional integer, default 1.
      limit: Optional integer, default 10.
    """
    try:
        org, err = _resolve_org(request)
        if err:
            return err

        if not _is_org_admin_or_teacher(request, org):
            return Response({"detail": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

        # Determine if user is teacher and get profile
        teacher_profile = None
        try:
            teacher_profile = request.user.teacher_profile
            if teacher_profile.organization != org:
                teacher_profile = None
        except TeacherProfile.DoesNotExist:
            pass

        test_id = request.query_params.get("test_id")
        student_filter = request.query_params.get("student_filter", "").lower().strip()
        sort_field = request.query_params.get("sort_field", "score")
        if sort_field not in ["score", "completionTime", "submittedAt"]:
            sort_field = "score"
        sort_order = request.query_params.get("sort_order", "desc")
        desc = "-" if sort_order == "desc" else ""

        page = int(request.query_params.get("page", 1))
        limit = int(request.query_params.get("limit", 10))
        if page < 1:
            page = 1
        if limit < 1:
            limit = 10
        offset = (page - 1) * limit

        filters = Q(test__course__organization=org)
        if teacher_profile:
            filters &= Q(test__course__teacher=teacher_profile)
        if test_id:
            filters &= Q(test_id=test_id)

        # Base queryset
        attempts_qs = TestAttempt.objects.filter(filters).select_related(
            "test", "student", "student__user", "student__current_classroom"
        ).annotate(
            completion_time=Extract(F("submitted_at") - F("started_at"), "epoch") / 60.0
        )

        if student_filter:
            attempts_qs = attempts_qs.filter(
                Q(student__user__first_name__icontains=student_filter) |
                Q(student__user__last_name__icontains=student_filter) |
                Q(student__user__email__icontains=student_filter)
            )

        # Total count before slicing
        total = attempts_qs.count()

        # Sort and paginate
        sort_map = {
            "score": "score",
            "completionTime": "completion_time",
            "submittedAt": "submitted_at",
        }
        attempts = attempts_qs.order_by(f"{desc}{sort_map[sort_field]}")[offset:offset + limit]

        performances = []
        for attempt in attempts:
            student = attempt.student
            user = student.user
            name = user.get_full_name() or user.email
            class_grade = student.current_classroom.name if student.current_classroom else "N/A"
            total_marks = attempt.test.total_marks or Decimal("0")
            percentage = (attempt.score / total_marks * 100) if total_marks else Decimal("0")
            completion_time = attempt.completion_time if attempt.submitted_at else 0
            status = "Passed" if attempt.score >= total_marks * Decimal("0.7") else "Failed"
            submitted_at = attempt.submitted_at.isoformat() if attempt.submitted_at else ""

            performances.append({
                "id": str(attempt.id),
                "studentName": name,
                "studentId": student.admission_no or str(student.id),
                "email": user.email,
                "classGrade": class_grade,
                "score": float(attempt.score),
                "totalMarks": float(total_marks),
                "percentage": float(percentage),
                "completionTime": round(completion_time),
                "status": status,
                "submittedAt": submitted_at,
                "testId": str(attempt.test.id),
                "testTitle": attempt.test.title,
                # "answers": []  # exclude for list; fetch in detail if needed
            })

        return Response({
            "performances": performances,
            "pagination": {
                "page": page,
                "limit": limit,
                "total": total,
                "pages": (total + limit - 1) // limit if limit else 1,
            }
        })

    except Exception as e:
        traceback.print_exc()
        return Response({"detail": "Unexpected error", "error": str(e)},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def student_performance_detail(request):
    """
    GET /api/teacher/student-performance/detail/?id=<attempt_id>

    Returns detailed performance for a specific attempt ID.

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

        id = request.query_params.get("id")
        if not id:
            return Response({"detail": "id required."}, status=status.HTTP_400_BAD_REQUEST)

        # Fetch the attempt
        attempt = TestAttempt.objects.filter(id=id, test__course__organization=org).select_related(
            "test", "student", "student__user", "student__current_classroom", "test__course"
        ).first()
        if not attempt:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        # Teacher filter
        teacher_profile = None
        try:
            teacher_profile = request.user.teacher_profile
            if teacher_profile.organization != org:
                teacher_profile = None
        except TeacherProfile.DoesNotExist:
            pass

        if teacher_profile and attempt.test.course.teacher != teacher_profile:
            return Response({"detail": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

        student = attempt.student
        user = student.user
        test = attempt.test

        # Student info
        student_info = {
            "studentName": user.get_full_name() or user.email,
            "studentId": student.admission_no or str(student.id),
            "email": user.email,
            "classGrade": student.current_classroom.name if student.current_classroom else "N/A",
        }

        # Test summary
        total_marks = test.total_marks or Decimal("0")
        percentage = (attempt.score / total_marks * 100) if total_marks else Decimal("0")
        completion_time = 0
        if attempt.submitted_at:
            delta = attempt.submitted_at - attempt.started_at
            completion_time = delta.total_seconds() / 60.0
        attempt_status = "Passed" if attempt.score >= total_marks * Decimal("0.7") else "Failed"
        submitted_at = attempt.submitted_at.isoformat() if attempt.submitted_at else None

        test_summary = {
            "testTitle": test.title,
            "score": float(attempt.score),
            "totalMarks": float(total_marks),
            "percentage": float(percentage),
            "status": attempt_status,
            "completionTime": round(completion_time),
            "submittedAt": submitted_at,
        }

        # Answer details
        answers = []
        questions = Question.objects.filter(test=test).order_by("order").prefetch_related("choices")
        student_answers = attempt.answers  # dict {q_id: value}
        for q in questions:
            if len(student_answers) < 1:
                return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
            print(student_answers)
            selected = student_answers[0].get(str(q.id), "")
            if q.qtype in ["scq", "mcq", "tf"]:
                choices = list(q.choices.order_by("order"))
                options = [c.text for c in choices]
                correct_indices = [i for i, c in enumerate(choices) if c.is_correct]
                correct = ", ".join(options[i] for i in correct_indices)
                if q.qtype == "mcq":
                    selected_indices = selected if isinstance(selected, list) else [selected]
                    selected_indices = [int(s) for s in selected_indices if str(s).isdigit()]
                    selected_text = ", ".join(options[i] for i in selected_indices if 0 <= i < len(options))
                    status_q = "Correct" if set(selected_indices) == set(correct_indices) else "Incorrect"
                else:  # scq or tf
                    if q.qtype == "tf" and not str(selected).isdigit():
                        selected_index = 1 if str(selected).lower() == "true" else 0
                    else:
                        selected_index = int(selected) if str(selected).isdigit() else -1
                    selected_text = options[selected_index] if 0 <= selected_index < len(options) else str(selected)
                    status_q = "Correct" if selected_index in correct_indices else "Incorrect"
            else:  # short, essay
                correct = ""
                selected_text = str(selected)
                status_q = "Pending"  # or based on if graded

            answers.append({
                "question": q.body,
                "selected": selected_text,
                "correct": correct,
                "status": status_q,
            })


        payload = {
            "student": student_info,
            "test": test_summary,
            "answers": answers,
        }
        return Response(payload)

    except Exception as e:
        traceback.print_exc()
        return Response({"detail": "Unexpected error", "error": str(e)},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR)




@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def teacher_module_analytics(request):
    """
    Returns paginated module analytics for the authenticated teacher.
    Enrollment totals are the number of people enrolled in the module's course.
    Uses subqueries to avoid join fan-out.
    """
    try:
        # --- auth & teacher/org lookup ---
        session_token = request.headers.get("X-Session-Token")
        if not session_token:
            return Response({"error": "Session token required"}, status=status.HTTP_400_BAD_REQUEST)

        token = get_object_or_404_ajax(
            SessionToken,
            key=session_token,
            is_active=True,
            expires_at__gt=timezone.now()
        )
        if not token:
            return Response({"error": "Invalid or expired session token"}, status=status.HTTP_401_UNAUTHORIZED)

        user = token.user
        teacher = get_object_or_404_ajax(TeacherProfile, user=user)
        if not teacher:
            return Response({"error": "Teacher profile not found"}, status=status.HTTP_403_FORBIDDEN)

        org = teacher.organization

        # --- filters & pagination ---
        search      = request.query_params.get("search", "")
        difficulty  = request.query_params.get("difficulty", "").upper()
        active      = request.query_params.get("active", "true") == "true"

        try:
            page = max(1, int(request.query_params.get("page", 1)))
        except Exception:
            page = 1
        try:
            page_size = max(1, min(50, int(request.query_params.get("page_size", 10))))
        except Exception:
            page_size = 10

        base_qs = Module.objects.filter(course__organization=org, course__teacher=teacher)
        if search:
            base_qs = base_qs.filter(Q(name__icontains=search) | Q(description__icontains=search))
        if difficulty:
            base_qs = base_qs.filter(difficulty=difficulty)
        if active:
            base_qs = base_qs.filter(active=True)

        total_count = base_qs.count()
        total_pages = math.ceil(total_count / page_size) if page_size else 1

        # --- subqueries: per-module course enrollments & avg completion ---
        enroll_qs = (
            Enrollment.objects
            .filter(course=OuterRef("course"), status=Enrollment.Status.ACTIVE)
            .values("course")
            .annotate(c=Count("id"))
            .values("c")
        )
        avg_qs = (
            Enrollment.objects
            .filter(
                course=OuterRef("course"),
                status__in=[Enrollment.Status.ACTIVE, Enrollment.Status.COMPLETED]
            )
            .values("course")
            .annotate(a=Avg("progress_pct"))
            .values("a")
        )

        # --- order, annotate, then slice ---
        start = (page - 1) * page_size
        end   = page * page_size

        page_qs = (
            base_qs.select_related("course", "category")
                   .annotate(
                       lesson_count=Count("lessons", filter=Q(lessons__active=True), distinct=True),
                       enrollments=Coalesce(Subquery(enroll_qs, output_field=IntegerField()), 0),
                       avg_completion=Coalesce(Subquery(avg_qs, output_field=FloatField()), 0.0),
                   )
                   .order_by("-updated_at")[start:end]
        )

        # Force evaluation ONCE so we can safely compute aggregates without .distinct() on a sliced qs
        page_modules = list(page_qs)

        # ---------- AGGREGATES WITHOUT DOUBLE COUNTING COURSES ----------
        # Unique courses on THIS PAGE (dedupe in Python; no DB .distinct() after slice)
        course_ids_in_page = list({m.course_id for m in page_modules})

        # Total enrollments across unique courses on page
        if course_ids_in_page:
            per_course_enrollments = (
                Enrollment.objects
                .filter(course_id__in=course_ids_in_page, status=Enrollment.Status.ACTIVE)
                .values("course_id")
                .annotate(c=Count("id"))
            )
            total_enrollments = sum(row["c"] for row in per_course_enrollments)
            completion_rate = (
                Enrollment.objects
                .filter(
                    course_id__in=course_ids_in_page,
                    status__in=[Enrollment.Status.ACTIVE, Enrollment.Status.COMPLETED],
                )
                .aggregate(avg=Avg("progress_pct"))["avg"] or 0.0
            )
        else:
            total_enrollments = 0
            completion_rate = 0.0
        # ---------------------------------------------------------------

        modules_data = []
        for m in page_modules:
            modules_data.append({
                "id": m.id,
                "title": f"{m.name} ({m.course.name})",
                "description": m.description,
                "difficulty": m.difficulty,
                "category": {"id": m.category.id, "name": m.category.name} if m.category else None,
                "estimatedDuration": m.estimated_duration_in_minutes or 0,
                "order": m.order,
                "active": m.active,
                "isPublished": getattr(m, "is_published", False),
                "course": {"id": m.course.id, "name": m.course.name},
                "createdAt": m.created_at,
                "updatedAt": m.updated_at,
                "lessons": [],
                "lessonCount": m.lesson_count or 0,
                "enrollments": m.enrollments or 0,                 # per-module = course's enrollment
                "completion": float(m.avg_completion or 0.0),      # per-module avg from course
            })

        data = {
            "aggregates": {
                "total_enrollments": total_enrollments,  # counted once per course on the page
                "completion_rate": completion_rate,
            },
            "pagination": {
                "total_count": total_count,
                "total_pages": total_pages,
                "current_page": page,
                "page_size": page_size,
            },
            "modules": modules_data,
        }
        return Response(data, status=status.HTTP_200_OK)

    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)










