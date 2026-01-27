# texagon_academy\texagonbackend\core\management\commands\run_gamification.py
from django.core.management.base import BaseCommand
from django.db import models
from orgs.models import Organization
from gamification.models import AchievementDefinition, Badge
from gamification.services.rules import compute_rule_value, get_target
from gamification.services.engine import unlock_achievement, unlock_badge_if_eligible


class Command(BaseCommand):
    help = "Evaluate and award gamification + badges (fully dynamic rules)."

    def add_arguments(self, parser):
        parser.add_argument("--org", type=int, default=None)
        parser.add_argument("--student", type=int, default=None)

    def handle(self, *args, **opts):
        org_qs = Organization.objects.all()
        if opts["org"]:
            org_qs = org_qs.filter(id=opts["org"])

        for org in org_qs.iterator():
            # global + org definitions
            ach_defs = AchievementDefinition.objects.filter(
                is_active=True
            ).filter(
                models.Q(organization=org) | models.Q(organization__isnull=True)
            )

            badges = Badge.objects.filter(
                models.Q(organization=org) | models.Q(organization__isnull=True)
            ).order_by("points")

            # adjust this line to match your org -> student relation
            students = org.students.all()
            if opts["student"]:
                students = students.filter(id=opts["student"])

            for student in students.iterator():
                # Achievements
                for definition in ach_defs:
                    rule = definition.rule or {}
                    target = get_target(rule)
                    if target <= 0:
                        continue
                    value = compute_rule_value(
                        org_id=org.id,
                        student_id=student.id,
                        rule=rule,
                    )
                    if value >= target:
                        unlock_achievement(
                            student,
                            definition,
                            value=value,
                            meta={"rule": rule},
                        )

                # Badges
                for badge in badges:
                    unlock_badge_if_eligible(student, badge)

        self.stdout.write(self.style.SUCCESS("Gamification run complete."))
