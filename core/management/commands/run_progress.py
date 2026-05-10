# texagon_academy\texagonbackend\core\management\commands\run_progress.py
from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable, Tuple

from django.apps import apps
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from core.utils import resolve_season
from gamification.services.engine import log_event
from codeide.models import CodeProject
from learning.models import CoursePassCriteria, Enrollment
from assessments.models import TestAttempt


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
                    season_start = season_obj.start_at if season_obj else None
                    season_end = season_obj.end_at if season_obj else None

                    # ---------- CBT side (score + count) ----------
                    cbt_needed = int(criteria.no_of_cbt or 0)
                    cbt_total = Decimal(criteria.total_pass_mark_cbt or 0)

                    cbt_attempts_qs = (
                        TestAttempt.objects.filter(
                            test__course_id=enr.course_id,
                            student_id=enr.student_id,
                        )
                        .filter(Q(status="submitted") | Q(submitted_at__isnull=False))
                        .exclude(score__isnull=True)
                        .order_by("-score")
                    )

                    if season_start and season_end:
                        cbt_attempts_qs = cbt_attempts_qs.filter(
                            created_at__gte=season_start,
                            created_at__lt=season_end
                        )

                    if cbt_needed > 0:
                        # best N CBT attempts
                        cbt_scores = list(
                            cbt_attempts_qs.values_list("score", flat=True)
                        )
                        cbt_done = len(cbt_scores)
                        cbt_count_ratio = Decimal(cbt_done) / Decimal(cbt_needed)
                    else:
                        cbt_scores = []
                        cbt_done = 0
                        cbt_count_ratio = Decimal("1")

                    cbt_count_ratio = max(Decimal("0"), min(Decimal("1"), cbt_count_ratio))

                    cbt_marks = sum((Decimal(s or 0) for s in cbt_scores), Decimal("0"))

                    if cbt_total > 0:
                        if cbt_marks > cbt_total:
                            cbt_marks = cbt_total
                        cbt_score_ratio = cbt_marks / cbt_total
                    else:
                        cbt_score_ratio = Decimal("1")

                    cbt_score_ratio = max(Decimal("0"), min(Decimal("1"), cbt_score_ratio))

                    # ✅ combine score + count
                    cbt_ratio = cbt_score_ratio * cbt_count_ratio
                    cbt_ratio = max(Decimal("0"), min(Decimal("1"), cbt_ratio))

                    # ---------- Code side (score + count) ----------
                    code_needed = int(criteria.no_of_code_submission or 0)
                    code_total = Decimal(criteria.total_pass_mark_code or 0)

                    code_qs = (
                        CodeProject.objects.filter(
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
                        # best N graded submissions
                        code_scores = list(code_qs.values_list("score", flat=True))
                        code_done = len(code_scores)
                        code_count_ratio = Decimal(code_done) / Decimal(code_needed)
                    else:
                        code_scores = []
                        code_done = 0
                        code_count_ratio = Decimal("1")

                    code_count_ratio = max(Decimal("0"), min(Decimal("1"), code_count_ratio))

                    code_marks = sum((Decimal(s or 0) for s in code_scores), Decimal("0"))

                    if code_total > 0:
                        if code_marks > code_total:
                            code_marks = code_total
                        code_score_ratio = code_marks / code_total
                    else:
                        code_score_ratio = Decimal("1")

                    code_score_ratio = max(Decimal("0"), min(Decimal("1"), code_score_ratio))

                    # ✅ combine score + count
                    code_ratio = code_score_ratio * code_count_ratio
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

                    # Gamification trigger on transition to completed
                    if old_status != Enrollment.Status.COMPLETED and new_status == Enrollment.Status.COMPLETED:
                        student = enr.student
                        org = enr.course.organization

                        log_event(
                            student=student,
                            org=org,
                            event_type="course_completed",
                            value=1,
                            meta={
                                "course_id": enr.course_id,
                                "enrollment_id": enr.id,
                            },
                            dedupe_key=f"course_completed:org={org.id}:student={student.id}:course={enr.course_id}",
                            occurred_at=getattr(enr, "updated_at", None),
                        )
                        enr.completed_at = timezone.now()
                        if commit:
                            enr.save(update_fields=["completed_at", "updated_at"])

                    old_pct = Decimal(enr.progress_pct or 0).quantize(Decimal("0.01"))
                    changed = (old_pct != pct) or (enr.status != new_status)

                    if changed:
                        updated += 1
                        prev_status = enr.status

                        enr.progress_pct = pct
                        enr.status = new_status
                        if commit:
                            enr.save(update_fields=["progress_pct", "status", "updated_at"])
                        
                        
                        self.stdout.write(
                            f"[{enr.id}] student={enr.student_id} course={enr.course_id} | "
                            f"{old_pct}->{pct} | {prev_status}->{new_status} "
                            f"(cbt_done={cbt_done}/{cbt_needed}, code_done={code_done}/{code_needed}) "
                            f"(cbt_score={_q2(cbt_marks)}/{_q2(cbt_total)}, code_score={_q2(code_marks)}/{_q2(code_total)})"
                        )


                if not commit:
                    raise _DryRunRollback()

        except _DryRunRollback:
            pass

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. updated={updated}, skipped_no_criteria={skipped_no_criteria}, dry_run={not commit}"
            )
        )
