# api/views.py
from typing import Any, DefaultDict, Dict, List, Optional
from django.db import IntegrityError
from django.db.utils import DataError
import traceback
from collections import defaultdict
from decimal import Decimal
from django.conf import settings
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone
from datetime import datetime
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.response import Response
from rest_framework_api_key.permissions import HasAPIKey

from api.authentication import SessionTokenAuthentication

# Project models
from academics.models import StudentProfile,TeacherProfile
from assessments.models import Test, Question, Choice, TestAttempt,TestAnswer
from learning.models import Enrollment, Course
from orgs.models import OrganizationMembership
import logging
from datetime import timezone as dt_timezone



logger = logging.getLogger(__name__)

def _has_field(model, field: str) -> bool:
    try:
        model._meta.get_field(field)
        return True
    except Exception:
        return False


def _get_student_for_user(user) -> Optional[StudentProfile]:
    """Prefer the active organization; fallback to any student profile for the user."""
    mem = (OrganizationMembership.objects
           .filter(user=user, is_active=True)
           .select_related("organization")
           .order_by("-id")
           .first())
    if mem:
        sp = StudentProfile.objects.filter(user=user, organization=mem.organization).first()
        if sp:
            return sp
    return StudentProfile.objects.filter(user=user).order_by("-id").first()


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
    """Normalize to: multiple-choice | true-false | short-answer | essay"""
    if _has_field(Question, "qtype") and getattr(q, "qtype", None):
        raw = str(getattr(q, "qtype")).lower().replace("_", "-")
        mapping = {
            "mcq": "multiple-choice",
            "multiple-choice": "multiple-choice",
            "multiple_choice": "multiple-choice",
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
        return "multiple-choice"
    # No choices provided:
    return "short-answer"


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

        qs = Test.objects.filter(course_id__in=course_ids)

        # Optional: filter by course
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
            qs = qs.filter(Q(start_at__isnull=True) | Q(start_at__gte=now))

        # Collect test ids
        tests = list(qs.select_related("course"))
        test_ids = [t.id for t in tests]
        if not test_ids:
            return Response({"tests": []}, status=status.HTTP_200_OK)

        # -------- Fetch questions & choices in bulk (avoid N+1) --------
        questions = list(Question.objects.filter(test_id__in=test_ids))
        # Group questions by test_id
        questions_by_test: DefaultDict[int, List[Question]] = defaultdict(list)
        for q in questions:
            questions_by_test[q.test_id].append(q)

        # Fetch choices once and group by question_id
        choice_qids = [q.id for q in questions]
        choices_map: DefaultDict[int, List[Choice]] = defaultdict(list)
        if choice_qids:
            for c in Choice.objects.filter(question_id__in=choice_qids):
                choices_map[c.question_id].append(c)

        # Natural ordering
        for tid in questions_by_test:
            questions_by_test[tid].sort(key=lambda q: (_question_order(q), q.id))
        for qid in choices_map:
            choices_map[qid].sort(key=lambda c: (_choice_order(c), c.id))

        # Whether to include correct answers (admin/debug use only)
        include_answers = request.query_params.get("include_answers") in {"1", "true", "True"}

        # Sort tests: scheduled first by start time, else newest first
        if _has_field(Test, "start_at"):
            tests.sort(key=lambda t: (getattr(t, "start_at", None) or timezone.datetime.max.replace(tzinfo=dt_timezone.utc), -t.id))
        else:
            tests.sort(key=lambda t: -t.id)

        # ------------ Build payload ------------
        items: List[Dict[str, Any]] = []
        for t in tests:
            test_qs = questions_by_test.get(t.id, [])
            q_count = len(test_qs)

            # Human id (slug or fallback)
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

            # Build question objects
            questions_out: List[Dict[str, Any]] = []
            for q in test_qs:
                q_choices = choices_map.get(q.id, [])
                choice_objs = [{"id": c.id, "text": getattr(c, "text", str(c))} for c in q_choices]
                choice_texts = [co["text"] for co in choice_objs]

                qtype_norm = _map_qtype(q, len(q_choices), choice_texts)

                q_out: Dict[str, Any] = {
                    "id": q.id,
                    "type": qtype_norm,                               # "multiple-choice" | "true-false" | "short-answer" | "essay"
                    "question": _question_text(q),
                    "points": int(getattr(q, "points", 0) or 0),
                    "choices": choice_objs,                           # [{id, text}] - use id when submitting
                    "options": choice_texts,                          # convenience for UI radios
                }

                if include_answers:
                    # Safe reveal of correct choices when explicitly requested
                    # (Do NOT enable for student calls)
                    if _has_field(Choice, "is_correct"):
                        correct_ids = [c.id for c in q_choices if getattr(c, "is_correct", False)]
                        # For single-answer MCQ/TF, expose index of the first correct option too
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
                "items": questions_out,      # <-- all questions with options
            })

        return Response({"tests": items}, status=status.HTTP_200_OK)

    except Exception as e:
        payload = {
            "detail": "Error while fetching available tests.",
            "error": f"{type(e).__name__}: {e}",
        }
        if request.query_params.get("debug") in {"1", "true", "True"} or getattr(settings, "DEBUG", False):
            payload["traceback"] = traceback.format_exc()
        return Response(payload, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


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
        'scq': 'multiple-choice',
        'mcq': 'multiple-choice', 
        'tf': 'true-false',
        'true_false': 'true-false',
        'short': 'short-answer',
        'essay': 'essay'
    }
    
    qtype = getattr(question, 'qtype', 'scq')
    frontend_type = qtype_mapping.get(qtype, 'multiple-choice')
    
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
        elif frontend_type == 'multiple-choice':
            # For multiple choice, get index of correct answer
            correct_choices = [i for i, choice in enumerate(choices) if getattr(choice, 'is_correct', False)]
            if correct_choices:
                result['correctAnswer'] = correct_choices[0]  # Use first correct answer for single choice
        # For short-answer and essay, correctAnswer might be in meta
        elif frontend_type in ['short-answer', 'essay']:
            result['correctAnswer'] = (question.meta or {}).get('correct_answer', '')
    
    return result


def _serialize_test(test: Test, include_questions: bool = False) -> Dict[str, Any]:
    """Serialize a test object."""
    questions_count = test.questions.count() if hasattr(test, 'questions') else 0
    total_points = test.questions.aggregate(total=Sum('points'))['total'] or 0
    
    result = {
        'id': str(test.id),
        'title': getattr(test, 'title', f'Test #{test.id}'),
        'description': getattr(test, 'description', ''),
        'duration': int(getattr(test, 'duration_minutes', 30) or 30),
        'totalPoints': int(total_points),
        'difficulty': (test.settings or {}).get('difficulty', 'Medium'),
        'category': getattr(test.course, 'name', '') if hasattr(test, 'course') and test.course else '',
        'isPublished': getattr(test, 'visibility', 'draft') == 'published',
        'questionsCount': questions_count,
        'createdAt': test.created_at.isoformat() if hasattr(test, 'created_at') else None,
        'updatedAt': test.updated_at.isoformat() if hasattr(test, 'updated_at') else None,
    }
    
    if include_questions:
        questions = list(test.questions.all().order_by('order', 'id'))
        result['questions'] = [_serialize_question(q) for q in questions]
    
    return result


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
        "title": "Test Title",
        "instructions": "Test instructions",
        "duration": 60,
        "difficulty": "Medium",
        "course_id": 123,
        "category": "Math"
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


@api_view(["PUT", "PATCH"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
@transaction.atomic
def update_test(request, test_id: int):
    """Update test details."""
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
        
        # Update fields
        if 'title' in data:
            test.title = data['title'].strip()
        if 'instructions' in data:
            test.instructions = data['instructions']
        if 'duration' in data:
            test.duration_minutes = max(1, int(data['duration']))
        
        # Update settings
        settings = test.settings or {}
        if 'difficulty' in data:
            settings['difficulty'] = data['difficulty']
        if 'category' in data:
            settings['category'] = data['category']
        test.settings = settings
        
        test.save()
        
        serialized_test = _serialize_test(test, include_questions=True)
        
        return Response({
            "test": serialized_test,
            "message": "Test updated successfully."
        }, status=status.HTTP_200_OK)
        
    except Exception as e:
        payload = {
            "detail": "Error updating test.",
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
        "type": "multiple-choice",
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
        
        question_type = data.get('type', 'multiple-choice')
        points = max(1, int(data.get('points', 1)))
        
        # Map frontend types to backend types
        type_mapping = {
            'multiple-choice': 'scq',
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
        
        if options and question_type in ['multiple-choice', 'true-false']:
            for i, option_text in enumerate(options):
                is_correct = False
                if question_type == 'true-false':
                    is_correct = (option_text.lower() == 'true' and correct_answer is True) or \
                                (option_text.lower() == 'false' and correct_answer is False)
                elif question_type == 'multiple-choice' and isinstance(correct_answer, int):
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
                test_id=test_id,
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
            question_type = data.get('type', 'multiple-choice')
            
            for i, option_text in enumerate(options):
                is_correct = False
                if question_type == 'true-false':
                    is_correct = (option_text.lower() == 'true' and correct_answer is True) or \
                                (option_text.lower() == 'false' and correct_answer is False)
                elif question_type == 'multiple-choice' and isinstance(correct_answer, int):
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


















