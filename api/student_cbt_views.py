from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from django.utils import timezone
from django.db.models import Subquery, Prefetch
from decimal import Decimal
import uuid

from .authentication import SessionTokenAuthentication
from assessments.models import Test, TestAttempt, Question, Choice, TestAnswer
from academics.models import StudentProfile
from learning.models import Enrollment

def _get_student(request):
    user = request.user
    student = getattr(user, "student_profile", None)
    if isinstance(student, StudentProfile):
        return student
    return None

@api_view(["GET"])
@permission_classes([IsAuthenticated])
@authentication_classes([SessionTokenAuthentication])
def student_cbt_list(request):
    student = _get_student(request)
    if not student:
        return Response({"detail": "Only students can access this."}, status=status.HTTP_403_FORBIDDEN)

    from billing.models import UserAccountSubscription
    from django.utils import timezone as tz

    now = tz.now()

    # Determine subscription status once, reuse for all tests
    subscription_active = UserAccountSubscription.has_subscription(student.user, student.organization)
    allow_unsubscribed = getattr(student.organization, "allow_unsubscribed_users", False)

    enrolled_course_ids = Enrollment.objects.filter(
        student=student,
        status=Enrollment.Status.ACTIVE,
    ).values("course_id")

    tests = Test.objects.filter(
        visibility="published",
        course_id__in=Subquery(enrolled_course_ids)
    ).select_related("course").prefetch_related(
        "questions",
        "questions__choices"
    ).order_by("-created_at")

    # Serialize tests
    test_data = []
    for t in tests:
        items = []
        if t.mode == "offline":
            for q in t.questions.all():
                choices_data = []
                for c in q.choices.all():
                    choices_data.append({
                        "id": c.id,
                        "text": c.text,
                    })
                items.append({
                    "id": q.id,
                    "question": q.body,
                    "type": q.qtype,
                    "points": float(q.points),
                    "choices": choices_data,
                })

        # Compute per-test access — mirrors my_courses logic.
        # Use getattr() throughout so missing Course fields never crash the view.
        try:
            has_course_general_access = bool(
                getattr(t.course, 'course_type', '') == 'public'
                and getattr(t.course, 'general_activation', False)
                and (
                    getattr(t.course, 'general_activation_date', None) is None
                    or t.course.general_activation_date > now
                )
            ) if t.course else False
        except Exception:
            has_course_general_access = False
        has_access = subscription_active or allow_unsubscribed or has_course_general_access

        test_data.append({
            "id": f"test-{t.id}",
            "pk": t.id,
            "title": t.title,
            "description": t.instructions,
            "type": "quiz",  # default, can map from test.type if it exists
            "mode": t.mode or "online",
            "difficulty": "beginner",  # defaults
            "duration": f"{int(t.duration_minutes)} mins" if getattr(t, "duration_minutes", None) else None,
            "total_marks": float(t.total_marks),
            "show_score": getattr(t, "show_score", True),
            "questions": t.questions.count(),
            "course": t.course.name if t.course else None,
            "course_id": t.course_id,  # ← numeric ID so Flutter can cross-reference
            "has_access": has_access,  # ← definitive access flag from the server
            "startsAt": t.start_at.isoformat() if getattr(t, "start_at", None) else None,
            "endsAt": t.end_at.isoformat() if getattr(t, "end_at", None) else None,
            "requiresSubscription": not has_access,
            "items": items
        })

    attempts_qs = TestAttempt.objects.filter(student=student).select_related("test", "test__course").order_by("-started_at")
    
    attempts_data = []
    results_map = {}
    
    for a in attempts_qs:
        attempt_obj = {
            "id": a.id,
            "test_id": a.test_id,
            "started_at": a.started_at.isoformat() if a.started_at else None,
            "submitted_at": a.submitted_at.isoformat() if a.submitted_at else None,
            "score": float(a.score),
            "status": a.status,
            "test": {
                "id": a.test.id,
                "title": a.test.title,
                "course_name": a.test.course.name if a.test.course else None,
                "total_marks": float(a.test.total_marks),
                "show_score": getattr(a.test, "show_score", True),
            }
        }
        attempts_data.append(attempt_obj)
        
        # Results Map (only if graded/submitted) — keys must match CbtResult.fromJson
        if a.status in ["graded", "submitted"]:
            total_marks = float(a.test.total_marks) if a.test.total_marks else 0
            score = float(a.score)
            results_map[str(a.test_id)] = {
                "title": a.test.title,
                "score": score,
                "total_points": total_marks,
                "percentage": round((score / total_marks * 100)) if total_marks else 0,
                "answered": 0,
                "pending_manual": 0,
                "status": a.status,
                "show_score": getattr(a.test, "show_score", True),
            }

    return Response({
        "tests": test_data,
        "results": results_map,
        "attempts": {
            "results": attempts_data,
            "count": len(attempts_data),
            "page": 1,
            "page_size": 20
        }
    })


@api_view(["POST"])
@permission_classes([IsAuthenticated])
@authentication_classes([SessionTokenAuthentication])
def student_cbt_start(request):
    student = _get_student(request)
    if not student:
        return Response({"detail": "Only students can access this."}, status=status.HTTP_403_FORBIDDEN)

    test_id = request.data.get("testId")
    if not test_id:
        return Response({"detail": "testId is required"}, status=status.HTTP_400_BAD_REQUEST)

    try:
        test = Test.objects.prefetch_related(
            Prefetch("questions", queryset=Question.objects.order_by("order")),
            Prefetch("questions__choices", queryset=Choice.objects.order_by("order"))
        ).get(id=test_id)
    except Test.DoesNotExist:
        return Response({"detail": "Test not found"}, status=status.HTTP_404_NOT_FOUND)

    # Check for existing in_progress attempt
    attempt = TestAttempt.objects.filter(student=student, test=test, status="in_progress").first()
    now = timezone.now()
    if not attempt:
        attempt = TestAttempt.objects.create(
            student=student,
            test=test,
            started_at=now,
            status="in_progress",
        )
    
    duration_secs = test.duration_minutes * 60
    expires_at = attempt.started_at + timezone.timedelta(seconds=duration_secs)

    questions_data = []
    for q in test.questions.all():
        choices_data = []
        for c in q.choices.all():
            choices_data.append({
                "id": c.id,
                "text": c.text,
                # Do NOT include is_correct
            })
        questions_data.append({
            "id": q.id,
            "question": q.body,  # Flutter expects "question" not "body"
            "type": q.qtype,     # e.g. "scq", "mcq", "tf", "short", "essay"
            "points": float(q.points),
            "choices": choices_data,
        })

    return Response({
        "attemptId": attempt.id,
        "startedAtIso": attempt.started_at.isoformat(),
        "durationSeconds": duration_secs,
        "expiresAtMs": int(expires_at.timestamp() * 1000),
        "serverNowMs": int(now.timestamp() * 1000),
        "questions": questions_data,
    })


@api_view(["POST"])
@permission_classes([IsAuthenticated])
@authentication_classes([SessionTokenAuthentication])
def student_cbt_submit(request):
    student = _get_student(request)
    if not student:
        return Response({"detail": "Only students can access this."}, status=status.HTTP_403_FORBIDDEN)

    payload = request.data
    test_id = payload.get("currentTest")
    
    try:
        test = Test.objects.prefetch_related("questions__choices").get(id=test_id)
    except Test.DoesNotExist:
        return Response({"detail": "Test not found"}, status=status.HTTP_404_NOT_FOUND)

    attempt_id = payload.get("attempt_id")
    if attempt_id:
        try:
            attempt = TestAttempt.objects.get(id=attempt_id, student=student)
        except TestAttempt.DoesNotExist:
            attempt = TestAttempt.objects.filter(student=student, test=test).order_by("-started_at").first()
    else:
        attempt = TestAttempt.objects.filter(student=student, test=test).order_by("-started_at").first()

    if not attempt:
        attempt = TestAttempt.objects.create(
            student=student,
            test=test,
            started_at=timezone.now(),
            status="in_progress",
        )

    if attempt.status in ["submitted", "graded"]:
        # already submitted
        return Response({"detail": "Attempt already submitted."}, status=status.HTTP_400_BAD_REQUEST)

    answers_payload = payload.get("answers", [])
    
    # Grading logic
    total_score = Decimal("0.0")
    test_answers_to_create = []

    questions = {q.id: q for q in test.questions.all()}
    
    for ans in answers_payload:
        q_id = ans.get("question")
        question = questions.get(q_id)
        if not question:
            continue
            
        choice_val = ans.get("choice") # single choice ID
        choices_val = ans.get("choices") # list of choice IDs for MCQ
        text_val = ans.get("text", "")
        
        awarded = Decimal("0.0")
        is_auto = False
        selected_choice = None
        selected_choice_ids = []
        
        if question.qtype in ["scq", "tf"]:
            is_auto = True
            if choice_val:
                selected_choice = next((c for c in question.choices.all() if c.id == choice_val), None)
                if selected_choice and selected_choice.is_correct:
                    awarded = question.points
        elif question.qtype == "mcq":
            is_auto = True
            selected_choice_ids = choices_val or []
            correct_choice_ids = {c.id for c in question.choices.all() if c.is_correct}
            # All selected must be correct, and all correct must be selected
            if set(selected_choice_ids) == correct_choice_ids and len(correct_choice_ids) > 0:
                awarded = question.points
        else:
            # short/essay -> manual grading
            pass
            
        total_score += awarded
        
        test_answers_to_create.append(TestAnswer(
            attempt=attempt,
            question=question,
            selected_choice=selected_choice,
            selected_choice_ids=selected_choice_ids,
            answer_text=text_val,
            awarded_points=awarded,
            is_auto_graded=is_auto
        ))

    TestAnswer.objects.bulk_create(test_answers_to_create, ignore_conflicts=True)
    
    attempt.submitted_at = timezone.now()
    attempt.score = total_score
    attempt.status = "graded" # For now, mark as graded immediately if all were auto
    attempt.auto_submitted = payload.get("auto_submitted", False)
    attempt.save()

    total_marks = float(test.total_marks) if test.total_marks else 0
    final_score = float(attempt.score)
    return Response({
        "result": {
            "title": test.title,
            "score": final_score,
            "total_points": total_marks,
            "percentage": round((final_score / total_marks * 100)) if total_marks else 0,
            "answered": len(test_answers_to_create),
            "pending_manual": 0,
            "status": attempt.status,
            "show_score": getattr(test, "show_score", True),
        }
    })
