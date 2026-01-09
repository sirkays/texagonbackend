# learning/management/commands/recalc_enrollment_progress.py
from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable, Tuple
from django.utils import timezone
from django.apps import apps
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q
from gamification.services.engine import log_event
from core.utils import resolve_season


def _get_model_any(candidates: Iterable[Tuple[str, str]]):
    """
    Resolve a model even if it's located in a different app in your project.
    Tries (app_label, model_name) pairs in order.
    """
    last_err = None
    for app_label, model_name in candidates:
        try:
            return apps.get_model(app_label, model_name)
        except LookupError as e:
            last_err = e
    raise last_err or LookupError("Model not found in any candidate app labels.")


def _q2(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@contextmanager
def _noop_atomic():
    yield


class _DryRunRollback(Exception):
    """Raised to rollback changes in dry-run mode."""


class Command(BaseCommand):
    help = (
        "Recalculate Enrollment.progress_pct for ALL enrollments that are NOT completed, "
        "based on CoursePassCriteria. If progress becomes 100, status is set to COMPLETED."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--course-id",
            type=int,
            default=None,
            help="Only recalc enrollments for this course id.",
        )
        parser.add_argument(
            "--student-id",
            type=int,
            default=None,
            help="Only recalc enrollments for this student profile id.",
        )
        parser.add_argument(
            "--commit",
            action="store_true",
            help="Actually write updates to DB. Otherwise runs as dry-run.",
        )
        parser.add_argument(
            "--chunk-size",
            type=int,
            default=200,
            help="Iterator chunk size (default 200).",
        )

    def handle(self, *args, **opts):
        course_id = opts["course_id"]
        student_id = opts["student_id"]
        commit = opts["commit"]
        chunk_size = opts["chunk_size"]

        Enrollment = apps.get_model("learning", "Enrollment")
        CoursePassCriteria = apps.get_model("learning", "CoursePassCriteria")

        # Your TestAttempt is in the code you shared (same file), but app label might differ in your project.
        TestAttempt = _get_model_any(
            [
                ("learning", "TestAttempt"),
                ("assessments", "TestAttempt"),
                ("tests", "TestAttempt"),
                ("exam", "TestAttempt"),
            ]
        )

        # Your CodeSubmission is in ide/models.py
        CodeSubmission = _get_model_any(
            [
                ("codeide", "CodeSubmission"),
            ]
        )

        # ✅ Only non-completed enrollments
        qs = (
            Enrollment.objects.select_related("course", "student")
            .exclude(status=Enrollment.Status.COMPLETED)
        )

        if course_id:
            qs = qs.filter(course_id=course_id)
        if student_id:
            qs = qs.filter(student_id=student_id)

        total = qs.count()
        if total == 0:
            self.stdout.write(self.style.WARNING("No matching non-completed enrollments found."))
            return

        self.stdout.write(f"Found {total} non-completed enrollment(s). Dry-run={not commit}")

        updated = 0
        skipped_no_criteria = 0

        atomic_ctx = transaction.atomic if commit else _noop_atomic

        try:
            with atomic_ctx():
                for enr in qs.iterator(chunk_size=chunk_size):
                    # criteria per course
                    try:
                        criteria = CoursePassCriteria.objects.get(course_id=enr.course_id)
                    except CoursePassCriteria.DoesNotExist:
                        skipped_no_criteria += 1
                        continue

                    season_obj = resolve_season(enr.student.organization, timezone.now())
                    season_start = None
                    season_end = None
                    if season_obj:
                        season_start = season_obj.start_at
                        season_end = season_obj.end_at

                    # ---------- CBT side ----------
                    cbt_needed = int(criteria.no_of_cbt or 0)
                    cbt_total = Decimal(criteria.total_pass_mark_cbt or 0)
                    # If your "submitted" logic differs, adjust this filter:
                    cbt_attempts_qs = (
                        TestAttempt.objects.filter(
                            test__course_id=enr.course_id,
                            student_id=enr.student_id,
                        )
                        .filter(Q(status="submitted") | Q(submitted_at__isnull=False))
                        .order_by("-score")
                    )

                    if season_start and season_end:
                        # choose a consistent timestamp field; created_at comes from TimeStampedModel
                        cbt_attempts_qs = cbt_attempts_qs.filter(created_at__gte=season_start, created_at__lt=season_end)


                    if cbt_needed > 0:
                        cbt_scores = list(
                            cbt_attempts_qs.values_list("score", flat=True)[:cbt_needed]
                        )
                    else:
                        cbt_scores = []

                    cbt_marks = sum((Decimal(s or 0) for s in cbt_scores), Decimal("0"))

                    if cbt_total > 0:
                        if cbt_marks > cbt_total:
                            cbt_marks = cbt_total
                        cbt_ratio = cbt_marks / cbt_total
                    else:
                        # no pass mark requirement => treat as satisfied
                        cbt_ratio = Decimal("1")

                    cbt_ratio = max(Decimal("0"), min(Decimal("1"), cbt_ratio))

                    # ---------- Code side ----------
                    code_needed = int(criteria.no_of_code_submission or 0)
                    code_total = Decimal(criteria.total_pass_mark_code or 0)

                    # If your graded logic differs, adjust this filter:
                    code_qs = (
                        CodeSubmission.objects.filter(
                            lesson__module__course_id=enr.course_id,
                            student_id=enr.student_id,
                        )
                        .filter(status="graded")
                        .exclude(score__isnull=True)
                        .order_by("-score")
                    )

                    if season_start and season_end:
                        code_qs = code_qs.filter(created_at__gte=season_start, created_at__lt=season_end)

                    if code_needed > 0:
                        code_scores = list(code_qs.values_list("score", flat=True)[:code_needed])
                    else:
                        code_scores = []

                    code_marks = sum((Decimal(s or 0) for s in code_scores), Decimal("0"))

                    if code_total > 0:
                        if code_marks > code_total:
                            code_marks = code_total
                        code_ratio = code_marks / code_total
                    else:
                        code_ratio = Decimal("1")

                    code_ratio = max(Decimal("0"), min(Decimal("1"), code_ratio))

                    # ---------- Final progress ----------
                    pct = ((cbt_ratio + code_ratio) / Decimal("2")) * Decimal("100")
                    pct = max(Decimal("0"), min(Decimal("100"), pct))
                    pct = _q2(pct)

                    # ✅ Auto-complete if progress is 100
                    old_status = enr.status
                    new_status = enr.status
                    if pct >= Decimal("100.00"):
                        pct = Decimal("100.00")
                        new_status = Enrollment.Status.COMPLETED

                    # AFTER you compute new_status, but BEFORE save (either is fine):
                    if old_status != Enrollment.Status.COMPLETED and new_status == Enrollment.Status.COMPLETED:
                        student = enr.student
                        org = enr.course.organization  # Course has organization FK

                        log_event(
                            student=student,
                            org=org,
                            event_type="course_completed",
                            value=1,
                            meta={
                                "course_id": enr.course_id,
                                "enrollment_id": enr.id,  # optional but helpful for debugging
                            },
                            dedupe_key=f"course_completed:org={org.id}:student={student.id}:course={enr.course_id}",
                            occurred_at=getattr(enr, "updated_at", None),  # optional; or omit to use now()
                        )
                        enr.completed_at = timezone.now()
                        enr.save()

                    old_pct = Decimal(enr.progress_pct or 0).quantize(Decimal("0.01"))
                    changed = (old_pct != pct) or (enr.status != new_status)
                    if changed:
                        updated += 1
                        old_status = enr.status

                        enr.progress_pct = pct
                        enr.status = new_status
                        if commit:
                            enr.save(update_fields=["progress_pct", "status", "updated_at"])

                        self.stdout.write(
                            f"[{enr.id}] student={enr.student_id} course={enr.course_id} | "
                            f"{old_pct}->{pct} | {old_status}->{new_status}"
                        )

                if not commit:
                    # ensure absolutely no writes in dry-run mode
                    raise _DryRunRollback()

        except _DryRunRollback:
            pass

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. updated={updated}, skipped_no_criteria={skipped_no_criteria}, dry_run={not commit}"
            )
        )
