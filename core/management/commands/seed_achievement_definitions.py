from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q

from orgs.models import Organization
from gamification.models import AchievementDefinition


DEFAULT_ACHIEVEMENTS = [
    # 1) First Steps (first activity day)
    {
        "code": "first_steps",
        "title": "First Steps",
        "description": "Complete your first learning activity.",
        "icon": "star",
        "category": "General",
        "points": 10,
        "is_active": True,
        "rule": {"metric": "count", "event_type": "daily_active", "target": 1, "window_days": None},
    },

    # 2) Code Starter (submit first exercise)
    {
        "code": "code_starter",
        "title": "Code Starter",
        "description": "Submit your first coding exercise.",
        "icon": "zap",
        "category": "Coding",
        "points": 15,
        "is_active": True,
        "rule": {"metric": "count", "event_type": "exercise_submitted", "target": 1, "window_days": None},
    },

    # 3) Code Warrior (submit many exercises)
    {
        "code": "code_warrior",
        "title": "Code Warrior",
        "description": "Submit lots of coding exercises to prove your skills.",
        "icon": "zap",
        "category": "Coding",
        "points": 50,
        "is_active": True,
        "rule": {"metric": "count", "event_type": "exercise_submitted", "target": 30, "window_days": None},
    },

    # 4) Mastery Badge (get 10 mastered exercises)
    {
        "code": "mastery_badge",
        "title": "Mastery Badge",
        "description": "Score 80+ on multiple graded exercises.",
        "icon": "trophy",
        "category": "Coding",
        "points": 60,
        "is_active": True,
        "rule": {"metric": "count", "event_type": "exercise_mastered", "target": 10, "window_days": None},
    },

    # 5) Quiz Rookie (attempt first quiz)
    {
        "code": "quiz_rookie",
        "title": "Quiz Rookie",
        "description": "Attempt your first quiz.",
        "icon": "book",
        "category": "Quizzes",
        "points": 15,
        "is_active": True,
        "rule": {"metric": "count", "event_type": "quiz_attempted", "target": 1, "window_days": None},
    },

    # 6) Quiz Passer (pass 5 quizzes)
    {
        "code": "quiz_passer",
        "title": "Quiz Passer",
        "description": "Pass multiple quizzes.",
        "icon": "check",
        "category": "Quizzes",
        "points": 40,
        "is_active": True,
        "rule": {"metric": "count", "event_type": "quiz_passed", "target": 5, "window_days": None},
    },

    # 7) Quiz Master (90%+ in 5 quizzes)
    {
        "code": "quiz_master",
        "title": "Quiz Master",
        "description": "Score 90% or higher in multiple quizzes.",
        "icon": "trophy",
        "category": "Quizzes",
        "points": 120,
        "is_active": True,
        "rule": {"metric": "count", "event_type": "quiz_90_plus", "target": 5, "window_days": None},
    },
    {
        "code": "streak_newbie",
        "title": "Streak Newbie",
        "description": "Maintain a learning streak for 3 consecutive days.",
        "icon": "flame",
        "category": "Consistency",
        "points": 20,
        "is_active": True,
        "rule": {"metric": "max", "event_type": "streak_current", "target": 3, "window_days": None},
    },
    # 8) Streak Champion (30 day streak)
    {
        "code": "streak_geek",
        "title": "Streak Geek",
        "description": "Maintain a learning streak for 15 consecutive days.",
        "icon": "flame",
        "category": "Consistency",
        "points": 50,
        "is_active": True,
        "rule": {"metric": "max", "event_type": "streak_current", "target": 15, "window_days": None},
    },

    {
        "code": "streak_champion",
        "title": "Streak Champion",
        "description": "Maintain a learning streak for 30 consecutive days.",
        "icon": "flame",
        "category": "Consistency",
        "points": 100,
        "is_active": True,
        "rule": {"metric": "max", "event_type": "streak_current", "target": 30, "window_days": None},
    },

    # 9) Course Conqueror (complete 3 courses)
    {
        "code": "course_conqueror",
        "title": "Course Conqueror",
        "description": "Complete multiple courses to level up.",
        "icon": "crown",
        "category": "Courses",
        "points": 120,
        "is_active": True,
        "rule": {
            "metric": "distinct_count",
            "event_type": "course_completed",
            "distinct_key": "course_id",
            "target": 3,
            "window_days": None,
        },
    },
]


class Command(BaseCommand):
    help = "Seed (create/update) AchievementDefinition rows from DEFAULT_ACHIEVEMENTS."

    def add_arguments(self, parser):
        parser.add_argument(
            "--org",
            type=int,
            default=None,
            help="Organization ID. If omitted, seed GLOBAL (organization=NULL).",
        )
        parser.add_argument(
            "--all-orgs",
            action="store_true",
            help="Seed these achievements for every organization (organization=org).",
        )
        parser.add_argument(
            "--deactivate-missing",
            action="store_true",
            help="If set, any existing AchievementDefinition not in DEFAULT_ACHIEVEMENTS for the scope will be set is_active=False.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would happen without writing to the DB.",
        )

    @transaction.atomic
    def handle(self, *args, **opts):
        dry_run = bool(opts["dry_run"])
        deactivate_missing = bool(opts["deactivate_missing"])

        # Determine target organizations
        target_orgs = []
        if opts["all_orgs"]:
            target_orgs = list(Organization.objects.all())
        elif opts["org"]:
            try:
                target_orgs = [Organization.objects.get(id=opts["org"])]
            except Organization.DoesNotExist:
                self.stdout.write(self.style.ERROR(f"Organization with id={opts['org']} not found."))
                return
        else:
            # GLOBAL scope (organization = NULL)
            target_orgs = [None]

        def upsert_one(org, payload):
            code = payload["code"]
            data = {
                "title": payload.get("title", "").strip(),
                "description": payload.get("description", "") or "",
                "icon": payload.get("icon", "star") or "star",
                "category": payload.get("category", "General") or "General",
                "points": int(payload.get("points", 0) or 0),
                "is_active": bool(payload.get("is_active", True)),
                "rule": payload.get("rule") or {},
            }

            scope_label = f"org={org.id}" if org else "GLOBAL"
            if dry_run:
                exists = AchievementDefinition.objects.filter(
                    organization=org, code=code
                ).exists()
                action = "UPDATE" if exists else "CREATE"
                self.stdout.write(f"[DRY-RUN] {action} {scope_label} code={code} data={data}")
                return

            obj, created = AchievementDefinition.objects.update_or_create(
                organization=org,
                code=code,
                defaults=data,
            )
            action = "Created" if created else "Updated"
            self.stdout.write(self.style.SUCCESS(f"{action} {scope_label} -> {obj.code}"))

        # Seed
        for org in target_orgs:
            for payload in DEFAULT_ACHIEVEMENTS:
                upsert_one(org, payload)

            # Optionally deactivate defs that aren't in the seed list (for this scope)
            if deactivate_missing:
                seed_codes = [a["code"] for a in DEFAULT_ACHIEVEMENTS]
                qs = AchievementDefinition.objects.filter(organization=org).exclude(code__in=seed_codes)
                if dry_run:
                    self.stdout.write(f"[DRY-RUN] Would deactivate {qs.count()} achievements for scope {org.id if org else 'GLOBAL'}")
                else:
                    updated = qs.update(is_active=False)
                    self.stdout.write(self.style.WARNING(
                        f"Deactivated {updated} achievements for scope {org.id if org else 'GLOBAL'}"
                    ))

        self.stdout.write(self.style.SUCCESS("Seeding AchievementDefinition complete."))
