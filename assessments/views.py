from typing import Any, DefaultDict, Dict, List, Optional
from collections import defaultdict
from decimal import Decimal
import logging
import traceback
from datetime import datetime, timezone as py_tz

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Count, Q, Sum
from django.db.utils import DataError
from django.utils import timezone, dateparse

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.response import Response
from rest_framework_api_key.permissions import HasAPIKey

from api.authentication import SessionTokenAuthentication
from academics.models import StudentProfile, TeacherProfile
from assessments.models import Test, Question, Choice, TestAttempt, TestAnswer
from learning.models import Enrollment, Course
from orgs.models import OrganizationMembership
from core.utils import _get_student_for_user
from django.utils.dateparse import parse_datetime
from .serializers import TestAttemptSerializer


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
    # Must be a student
    student = _get_student_for_user(request.user)
    if not student:
        return Response({"detail": "Only students can access their test attempts."},
                        status=status.HTTP_403_FORBIDDEN)

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


from django.db.models import Q, Count, Exists, OuterRef
from django.utils import timezone
# ...your other imports

@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def available_tests(request):
    """
    List tests available to the current student (by enrollments) and include all questions & options.

    Query params:
      - include_past: '1' to include tests whose start_at is in the past (default excludes past-start)
      - course: course id to filter
      - include_answers: '1' to include correct answers (off by default; do not use for students)
      - debug: '1' to include traceback on error (dev only)
    """
    try:
        user = request.user
        student = _get_student_for_user(user)
        if not student:
            return Response({"tests": [], "detail": "No student profile found."}, status=status.HTTP_200_OK)

        enrollments = (Enrollment.objects
                       .filter(student=student)
                       .only("id", "course_id"))
        course_ids = list(enrollments.values_list("course_id", flat=True))
        if not course_ids:
            return Response({"tests": []}, status=status.HTTP_200_OK)

        # Base queryset: tests in the student's courses and with schedule set
        qs = Test.objects.filter(course_id__in=course_ids, start_at__isnull=False, end_at__isnull=False)

        # Optional filter by one course
        course_filter = request.query_params.get("course")
        if course_filter:
            try:
                qs = qs.filter(course_id=int(course_filter))
            except ValueError:
                pass

        # Optional: exclude past tests if scheduled
        include_past = request.query_params.get("include_past") in {"1", "true", "True"}
        if _has_field(Test, "start_at") and not include_past:
            now = timezone.now()
            qs = qs.filter(
                (Q(start_at__isnull=True) | Q(start_at__lte=now)) &
                (Q(end_at__isnull=True) | Q(end_at__gte=now))
            )

        # ---------- NEW: exclude no-question tests & tests already taken ----------
        # Count questions and require at least 1
        qs = qs.annotate(qcount=Count("questions", distinct=True)).filter(qcount__gt=0)

        # Consider a test "taken" if the student has any attempt (you can narrow to submitted/graded if you prefer)
        student_attempts = TestAttempt.objects.filter(
            test_id=OuterRef("pk"),
            student=student,
        )
        qs = qs.annotate(has_attempt=Exists(student_attempts)).filter(has_attempt=False)
        # -------------------------------------------------------------------------

        # Collect test ids
        tests = list(qs.select_related("course"))
        test_ids = [t.id for t in tests]
        if not test_ids:
            return Response({"tests": []}, status=status.HTTP_200_OK)

        # -------- Fetch questions & choices in bulk (avoid N+1) --------
        questions = list(Question.objects.filter(test_id__in=test_ids))
        questions_by_test: DefaultDict[int, List[Question]] = defaultdict(list)
        for q in questions:
            questions_by_test[q.test_id].append(q)

        choice_qids = [q.id for q in questions]
        choices_map: DefaultDict[int, List[Choice]] = defaultdict(list)
        if choice_qids:
            for c in Choice.objects.filter(question_id__in=choice_qids):
                choices_map[c.question_id].append(c)

        for tid in questions_by_test:
            questions_by_test[tid].sort(key=lambda q: (_question_order(q), q.id))
        for qid in choices_map:
            choices_map[qid].sort(key=lambda c: (_choice_order(c), c.id))

        include_answers = request.query_params.get("include_answers") in {"1", "true", "True"}

        # Sort tests: scheduled first by start time, else newest first
        if _has_field(Test, "start_at"):
            far_future = datetime.max.replace(tzinfo=py_tz.utc)
            tests.sort(key=lambda t: (getattr(t, "start_at", None) or far_future, -t.id))
        else:
            tests.sort(key=lambda t: -t.id)

        # ------------ Build payload ------------
        items: List[Dict[str, Any]] = []
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

            questions_out: List[Dict[str, Any]] = []
            for q in test_qs:
                q_choices = choices_map.get(q.id, [])
                choice_objs = [{"id": c.id, "text": getattr(c, "text", str(c))} for c in q_choices]
                choice_texts = [co["text"] for co in choice_objs]

                qtype_norm = _map_qtype(q, len(q_choices), choice_texts)

                q_out: Dict[str, Any] = {
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

        return Response({"tests": items}, status=status.HTTP_200_OK)

    except Exception as e:
        payload = {
            "detail": "Error while fetching available tests.",
            "error": f"{type(e).__name__}: {e}",
        }
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
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
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
      "suspicious_activity": 2   # ignored unless you add fields for it
    """
    user = request.user
    student = _get_student_for_user(user)
    if not student:
        return Response({"detail": "Student profile not found for user."}, status=status.HTTP_400_BAD_REQUEST)

    try:
        test = Test.objects.get(pk=test_id)
    except Test.DoesNotExist:
        return Response({"detail": "Test not found."}, status=status.HTTP_404_NOT_FOUND)

    payload = request.data or {}
    answers_in: List[Dict[str, Any]] = payload.get("answers") or []
    if not isinstance(answers_in, list) or not answers_in:
        return Response({"detail": "answers must be a non-empty list."}, status=status.HTTP_400_BAD_REQUEST)

    started_at_iso = payload.get("started_at")
    duration_seconds = payload.get("duration_seconds")

    # membership check: accept only questions that belong to this test
    test_question_ids = set(Question.objects.filter(test=test).values_list("id", flat=True))

    # Total points from Questions
    total_points = Question.objects.filter(test=test).aggregate(total=Sum("points"))["total"] or Decimal(0)

    # Build normalized answers + grade
    score = Decimal(0)
    auto_graded_count = 0
    pending_manual = 0
    breakdown: List[Dict[str, Any]] = []
    normalized_answers: List[Dict[str, Any]] = []
    answer_rows: List[TestAnswer] = []

    # Preload all correct choice ids per question to avoid repeated queries
    correct_map = {
        qid: set(Choice.objects.filter(question_id=qid, is_correct=True).values_list("id", flat=True))
        for qid in test_question_ids
    }

    # Preload points per question
    points_map = dict(Question.objects.filter(test=test).values_list("id", "points"))

    for item in answers_in:
        qid = item.get("question")
        if qid not in test_question_ids:
            logger.info("Ignoring answer for question not in test: question=%s test=%s", qid, test_id)
            continue

        q_points = Decimal(points_map.get(qid, 0) or 0)
        awarded = Decimal(0)
        auto_graded = False

        selected_choice_id = None
        selected_choice_ids: List[int] = []
        answer_text = ""

        # ----- SCQ/TF -----
        if "choice" in item and item["choice"] is not None:
            selected_choice_id = int(item["choice"])
            auto_graded = True
            if selected_choice_id in correct_map.get(qid, set()):
                awarded = q_points

        # ----- MCQ -----
        elif "choices" in item and isinstance(item["choices"], list):
            try:
                selected_choice_ids = [int(x) for x in item["choices"]]
            except Exception:
                selected_choice_ids = []
            # all-or-nothing grading by default:
            auto_graded = True
            if set(selected_choice_ids) == correct_map.get(qid, set()):
                awarded = q_points

        # ----- Short/Essay -----
        elif "text" in item:
            answer_text = (item.get("text") or "").strip()
            # Optional: auto-grade if you store an expected text in meta.correct_text
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
                pending_manual += 1

        else:
            logger.info("No recognizable answer format for question id=%s item=%s", qid, item)

        score += awarded
        if auto_graded:
            auto_graded_count += 1

        # Keep normalized (for quick debug/exports)
        normalized_answers.append({
            "question": qid,
            "choice": selected_choice_id,
            "choices": selected_choice_ids,
            "text": answer_text,
            "awarded": float(awarded),
            "points": float(q_points),
            "auto_graded": auto_graded,
        })

        breakdown.append({
            "question": qid,
            "points": float(q_points),
            "awarded": float(awarded),
            "auto_graded": auto_graded,
        })

        # Prepare TestAnswer row (FKs only set if applicable)
        ans = TestAnswer(
            attempt=None,  # set after attempt is created
            question_id=qid,
            selected_choice_id=selected_choice_id,
            selected_choice_ids=selected_choice_ids,
            answer_text=answer_text,
            awarded_points=awarded,
            is_auto_graded=auto_graded,
        )
        answer_rows.append(ans)

    answered = len(answer_rows)
    percentage = int(round((float(score) / float(total_points)) * 100)) if total_points else 0
    pass_mark = 70  # change to test.pass_mark if you add that field
    result = "PASS" if percentage >= pass_mark else "FAIL"

    # Parse started_at
    now = timezone.now()
    started_at = None
    if started_at_iso:
        try:
            parsed = datetime.fromisoformat(str(started_at_iso).replace("Z", "+00:00"))
            started_at = parsed if parsed.tzinfo else timezone.make_aware(parsed)
        except Exception:
            logger.exception("Failed to parse started_at: %s", started_at_iso)
            started_at = None
    if not started_at and duration_seconds:
        try:
            started_at = now - timezone.timedelta(seconds=int(duration_seconds))
        except Exception:
            started_at = None

    # Create attempt
    try:
        attempt = TestAttempt.objects.create(
            test=test,
            student=student,
            started_at=started_at or now,
            submitted_at=now,
            score=score,
            answers=normalized_answers,  # keep for convenience; we also persist rows below
            status="submitted",
        )
    except (IntegrityError, DataError):
        logger.exception("Failed creating TestAttempt")
        return Response({"detail": "Server error creating attempt."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    # Attach attempt FK and save all answers
    for a in answer_rows:
        a.attempt = attempt
    TestAnswer.objects.bulk_create(answer_rows, ignore_conflicts=True)

    return Response({
        "attempt_id": attempt.id,
        "score": float(score),
        "total_points": float(total_points),
        "percentage": percentage,
        "result": result,
        "answered": answered,
        "auto_graded": auto_graded_count,
        "pending_manual": pending_manual,
        "breakdown": breakdown,
    }, status=status.HTTP_200_OK)




####################################
# api/teacher_cbt_views.py


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
        
        # Create test
        test_data = {
            'course': course,
            'title': title,
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


















