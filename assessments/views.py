# api/views.py
from typing import Any, Dict, List, Optional

from django.db import transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.response import Response
from rest_framework_api_key.permissions import HasAPIKey

from api.authentication import SessionTokenAuthentication

# Project models
from academics.models import StudentProfile
from assessments.models import Test, Question, Choice, TestAttempt
from learning.models import Enrollment
from orgs.models import OrganizationMembership
import traceback


def _has_field(model, field: str) -> bool:
    try:
        model._meta.get_field(field)
        return True
    except Exception:
        return False


def _get_student_for_user(user) -> Optional[StudentProfile]:
    """Prefer active org; fallback to any student profile."""
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


@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def available_tests(request):
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

    Expected JSON body:
    {
      "answers": [
        { "question": 101, "choice": 555 },           // MCQ/True-False (choice id)
        { "question": 102, "text": "short answer" },  // Short/Essay free text
        ...
      ],
      "started_at": "2025-08-23T12:00:00Z",          // optional ISO8601
      "duration_seconds": 1760,                      // optional
      "suspicious_activity": 2                       // optional
    }

    Returns summary:
    {
      "attempt_id": 123,
      "score": 8,
      "total_points": 12,
      "percentage": 66,
      "result": "FAIL",
      "answered": 5,
      "auto_graded": 4,
      "pending_manual": 1,     // e.g. essay items
      "breakdown": [
        { "question": 101, "points": 2, "awarded": 2, "auto_graded": true },
        ...
      ]
    }
    """
    user = request.user
    student = _get_student_for_user(user)
    if not student:
        return Response({"detail": "Student profile not found for user."},
                        status=status.HTTP_400_BAD_REQUEST)

    try:
        test = Test.objects.get(pk=test_id)
    except Test.DoesNotExist:
        return Response({"detail": "Test not found."}, status=status.HTTP_404_NOT_FOUND)

    payload = request.data or {}
    answers_in: List[Dict[str, Any]] = payload.get("answers") or []
    if not isinstance(answers_in, list) or not answers_in:
        return Response({"detail": "answers must be a non-empty list."},
                        status=status.HTTP_400_BAD_REQUEST)

    # Optional meta
    started_at_iso = payload.get("started_at")
    duration_seconds = payload.get("duration_seconds")
    suspicious_activity = int(payload.get("suspicious_activity") or 0)

    # Gather questions in this test for quick membership check
    test_qs = Question.objects.filter(test=test).values_list("id", flat=True)
    test_question_ids = set(test_qs)

    # Grade
    total_points = Question.objects.filter(test=test).aggregate(
        total=Sum("points")
    )["total"] or 0

    score = 0
    auto_graded_count = 0
    pending_manual = 0
    breakdown: List[Dict[str, Any]] = []
    normalized_answers: List[Dict[str, Any]] = []  # what we save

    for item in answers_in:
        qid = item.get("question")
        if qid not in test_question_ids:
            # ignore answers for questions not in this test
            continue

        q = Question.objects.select_related().get(pk=qid)
        awarded = 0
        auto_graded = False

        if "choice" in item and item["choice"] is not None:
            # MCQ / True-False
            try:
                choice = Choice.objects.get(pk=item["choice"], question=q)
                auto_graded = True
                if getattr(choice, "is_correct", False):
                    awarded = int(getattr(q, "points", 0) or 0)
            except Choice.DoesNotExist:
                pass

            normalized_answers.append({
                "question": q.id,
                "choice": item["choice"],
                "text": None,
                "awarded": awarded,
                "points": int(getattr(q, "points", 0) or 0),
                "auto_graded": auto_graded,
            })

        elif "text" in item:
            # Short answer / Essay (store text, usually pending manual grading)
            text_val = (item.get("text") or "").strip()
            # If your Question has a correct_text field, you can auto-grade short answers:
            correct_text = getattr(q, "correct_text", None)
            if correct_text:
                # very light check: contains (case-insensitive)
                if correct_text.lower() in text_val.lower():
                    awarded = int(getattr(q, "points", 0) or 0)
                    auto_graded = True
                else:
                    auto_graded = True  # still auto-graded to 0 if strict
            else:
                pending_manual += 1

            normalized_answers.append({
                "question": q.id,
                "choice": None,
                "text": text_val,
                "awarded": awarded,
                "points": int(getattr(q, "points", 0) or 0),
                "auto_graded": auto_graded,
            })

        else:
            # No recognizable answer format
            normalized_answers.append({
                "question": q.id,
                "choice": None,
                "text": None,
                "awarded": 0,
                "points": int(getattr(q, "points", 0) or 0),
                "auto_graded": False,
            })

        score += awarded
        if auto_graded:
            auto_graded_count += 1

        breakdown.append({
            "question": q.id,
            "points": int(getattr(q, "points", 0) or 0),
            "awarded": awarded,
            "auto_graded": auto_graded,
        })

    answered = len(normalized_answers)
    percentage = int(round((score / total_points) * 100)) if total_points else 0

    # Heuristic pass mark: 70% unless your Test model has a pass_mark field
    pass_mark = int(getattr(test, "pass_mark", 70) or 70)
    result = "PASS" if percentage >= pass_mark else "FAIL"

    # Create/Save attempt
    # NOTE: we detect optional fields (answers JSON, duration fields) to avoid FieldErrors.
    now = timezone.now()
    started_at = None
    if started_at_iso:
        try:
            started_at = timezone.make_aware(timezone.datetime.fromisoformat(started_at_iso.replace("Z", "+00:00")))
        except Exception:
            started_at = None
    if not started_at and duration_seconds:
        # infer started_at if duration provided
        try:
            started_at = now - timezone.timedelta(seconds=int(duration_seconds))
        except Exception:
            started_at = None

    attempt = TestAttempt.objects.create(
        test=test,
        student=student,
        started_at=started_at or now,
        submitted_at=now,
        score=score if _test_has_field(TestAttempt, "score") else None,
        status=getattr(TestAttempt, "Status", None).SUBMITTED if hasattr(TestAttempt, "Status") else getattr(TestAttempt, "status", "submitted"),
    )
    # Some projects define status choices as strings; adjust safely:
    if _test_has_field(TestAttempt, "status") and not hasattr(TestAttempt, "Status"):
        try:
            attempt.status = "submitted"
            attempt.save(update_fields=["status"])
        except Exception:
            pass

    # Save raw answers if TestAttempt has a JSON/Text field for them
    for candidate_field in ["answers", "answers_json", "responses", "payload", "data"]:
        if _test_has_field(TestAttempt, candidate_field):
            setattr(attempt, candidate_field, normalized_answers)
            try:
                attempt.save(update_fields=[candidate_field])
            except Exception:
                pass
            break

    # Optional: save duration / suspicious counts if fields exist
    if duration_seconds and _test_has_field(TestAttempt, "duration_seconds"):
        attempt.duration_seconds = int(duration_seconds)
        attempt.save(update_fields=["duration_seconds"])

    if _test_has_field(TestAttempt, "suspicious_activity"):
        try:
            attempt.suspicious_activity = int(suspicious_activity)
            attempt.save(update_fields=["suspicious_activity"])
        except Exception:
            pass

    # If you have an AttemptAnswer model you prefer to use, you can create rows here.
    # (Left out intentionally since your earlier models did not define it.)

    # Response
    return Response({
        "attempt_id": attempt.id,
        "score": score,
        "total_points": total_points,
        "percentage": percentage,
        "result": result,
        "answered": answered,
        "auto_graded": auto_graded_count,
        "pending_manual": pending_manual,
        "breakdown": breakdown,
    }, status=status.HTTP_200_OK)
