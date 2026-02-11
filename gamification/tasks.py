# gamification/tasks.py
from celery import shared_task
from django.db import models
from orgs.models import Organization

BATCH_SIZE = 200

@shared_task
def run_gamification_for_org(org_id: int):
    org = Organization.objects.get(id=org_id)
    student_ids = list(org.students.values_list("id", flat=True))

    for i in range(0, len(student_ids), BATCH_SIZE):
        run_gamification_for_students.delay(org_id, student_ids[i:i+BATCH_SIZE])


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def run_gamification_for_students(self, org_id: int, student_ids: list[int]):
    from gamification.models import AchievementDefinition, Badge
    from gamification.services.rules import compute_rule_value, get_target
    from gamification.services.engine import unlock_achievement, unlock_badge_if_eligible
    from academics.models import StudentProfile

    org = Organization.objects.get(id=org_id)

    ach_defs = AchievementDefinition.objects.filter(is_active=True).filter(
        models.Q(organization=org) | models.Q(organization__isnull=True)
    )

    badges = Badge.objects.filter(
        models.Q(organization=org) | models.Q(organization__isnull=True)
    ).order_by("points")

    students = StudentProfile.objects.filter(id__in=student_ids)

    for student in students.iterator():
        for definition in ach_defs:
            rule = definition.rule or {}
            target = get_target(rule)
            if target <= 0:
                continue

            value = compute_rule_value(org_id=org.id, student_id=student.id, rule=rule)
            if value >= target:
                unlock_achievement(student, definition, value=value, meta={"rule": rule})

        for badge in badges:
            unlock_badge_if_eligible(student, badge)
