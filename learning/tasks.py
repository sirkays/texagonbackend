# texagon_academy/texagonbackend/learning/tasks.py
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, List, Dict, Tuple

from celery import shared_task
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


class _NoopAtomic:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _noop_atomic():
    return _NoopAtomic()


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def recalc_progress_chunk(
    self,
    enrollment_ids: List[int],
    *,
    commit: bool = True,
) -> Dict[str, int]:
    if not enrollment_ids:
        return {"total": 0, "updated": 0, "skipped_no_criteria": 0}

    qs = (
        Enrollment.objects.select_related("course", "student")
        .filter(id__in=enrollment_ids)
        .exclude(status=Enrollment.Status.COMPLETED)
    )

    # Cache criteria per course
    criteria_map: Dict[int, Optional[CoursePassCriteria]] = {}
    # Cache season per org_id -> (start, end)
    season_window_map: Dict[int, Tuple[Optional[timezone.datetime], Optional[timezone.datetime]]] = {}

    updated = 0
    skipped_no_criteria = 0

    atomic_ctx = transaction.atomic if commit else _noop_atomic
    now = timezone.now()

    with atomic_ctx():
        for enr in qs.iterator(chunk_size=200):
            # ----- criteria per course -----
            if enr.course_id not in criteria_map:
                criteria_map[enr.course_id] = CoursePassCriteria.objects.filter(
                    course_id=enr.course_id
                ).first()

            criteria = criteria_map[enr.course_id]
            if not criteria:
                skipped_no_criteria += 1
                continue

            # ----- season window -----
            org_for_season = getattr(enr.student, "organization", None)
            org_id_for_season = getattr(org_for_season, "id", None)

            season_start = None
            season_end = None
            if org_id_for_season:
                if org_id_for_season not in season_window_map:
                    season_obj = resolve_season(org_for_season, now)
                    season_window_map[org_id_for_season] = (
                        season_obj.start_at if season_obj else None,
                        season_obj.end_at if season_obj else None,
                    )
                season_start, season_end = season_window_map[org_id_for_season]

            # ---------- CBT side ----------
            cbt_needed = int(getattr(criteria, "no_of_cbt", 0) or 0)
            cbt_total = Decimal(getattr(criteria, "total_pass_mark_cbt", 0) or 0)

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
                    created_at__lt=season_end,
                )

            if cbt_needed > 0:
                # ✅ best N
                cbt_scores = list(cbt_attempts_qs.values_list("score", flat=True)[:cbt_needed])
                cbt_done = len(cbt_scores)
                cbt_count_ratio = Decimal(cbt_done) / Decimal(cbt_needed)
            else:
                cbt_scores = []
                cbt_done = 0
                cbt_count_ratio = Decimal("1")

            cbt_count_ratio = max(Decimal("0"), min(Decimal("1"), cbt_count_ratio))

            cbt_marks = sum((Decimal(s or 0) for s in cbt_scores), Decimal("0"))

            if cbt_total > 0:
                cbt_marks = min(cbt_marks, cbt_total)
                cbt_score_ratio = cbt_marks / cbt_total
            else:
                cbt_score_ratio = Decimal("1")

            cbt_score_ratio = max(Decimal("0"), min(Decimal("1"), cbt_score_ratio))
            cbt_ratio = max(Decimal("0"), min(Decimal("1"), cbt_score_ratio * cbt_count_ratio))

            # ---------- Code side ----------
            code_needed = int(getattr(criteria, "no_of_code_submission", 0) or 0)
            code_total = Decimal(getattr(criteria, "total_pass_mark_code", 0) or 0)

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
                # ✅ best N
                code_scores = list(code_qs.values_list("score", flat=True)[:code_needed])
                code_done = len(code_scores)
                code_count_ratio = Decimal(code_done) / Decimal(code_needed)
            else:
                code_scores = []
                code_done = 0
                code_count_ratio = Decimal("1")

            code_count_ratio = max(Decimal("0"), min(Decimal("1"), code_count_ratio))

            code_marks = sum((Decimal(s or 0) for s in code_scores), Decimal("0"))

            if code_total > 0:
                code_marks = min(code_marks, code_total)
                code_score_ratio = code_marks / code_total
            else:
                code_score_ratio = Decimal("1")

            code_score_ratio = max(Decimal("0"), min(Decimal("1"), code_score_ratio))
            code_ratio = max(Decimal("0"), min(Decimal("1"), code_score_ratio * code_count_ratio))

            # ---------- Final progress ----------
            pct = ((cbt_ratio + code_ratio) / Decimal("2")) * Decimal("100")
            pct = _q2(max(Decimal("0"), min(Decimal("100"), pct)))

            old_status = enr.status
            new_status = enr.status
            completed_now = False

            if pct >= Decimal("100.00"):
                pct = Decimal("100.00")
                new_status = Enrollment.Status.COMPLETED
                completed_now = (old_status != Enrollment.Status.COMPLETED)

            old_pct = Decimal(enr.progress_pct or 0).quantize(Decimal("0.01"))
            changed = (old_pct != pct) or (enr.status != new_status)

            # Apply changes (single save)
            if changed or completed_now:
                enr.progress_pct = pct
                enr.status = new_status

                if completed_now:
                    enr.completed_at = timezone.now()

                    org = getattr(enr.course, "organization", None)
                    if org is not None:
                        log_event(
                            student=enr.student,
                            org=org,
                            event_type="course_completed",
                            value=1,
                            meta={"course_id": enr.course_id, "enrollment_id": enr.id},
                            dedupe_key=f"course_completed:org={org.id}:student={enr.student_id}:course={enr.course_id}",
                            occurred_at=getattr(enr, "updated_at", None),
                        )

                if commit:
                    fields = ["progress_pct", "status", "updated_at"]
                    if completed_now and hasattr(enr, "completed_at"):
                        fields.append("completed_at")
                    enr.save(update_fields=fields)

                updated += 1

    return {"total": len(enrollment_ids), "updated": updated, "skipped_no_criteria": skipped_no_criteria}
