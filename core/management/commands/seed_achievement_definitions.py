from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q

from orgs.models import Organization
from gamification.models import AchievementDefinition


DEFAULT_ACHIEVEMENTS = [
    # 1) First Steps (boolean-style)
    {
        "code": "first_steps",
        "title": "First Steps",
        "description": "Complete your first learning activity.",
        "icon": "star",
        "category": "General",
        "points": 10,
        "is_active": True,
        # Rule example: at least 1 learning activity event
        "rule": {
            "metric": "count",
            "event_type": "daily_active",
            "target": 1,
            "window_days": None,
        },
    },

    # 2) Code Warrior (solve N exercises)
    {
        "code": "code_warrior",
        "title": "Code Warrior",
        "description": "Solve coding exercises to prove your skills.",
        "icon": "zap",
        "category": "Coding",
        "points": 50,
        "is_active": True,
        "rule": {
            "metric": "count",
            "event_type": "exercise_solved",
            "target": 30,
            "window_days": None,
            # optional meta filters (enable if you want):
            # "filters": {"skill": "python"}
        },
    },

    # 3) Quiz Master (score >= 90 in N quizzes)
    # Recommended approach: log an event_type="quiz_90_plus" when score>=90%,
    # then the rule is a simple count.
    {
        "code": "quiz_master",
        "title": "Quiz Master",
        "description": "Score 90% or higher in multiple quizzes.",
        "icon": "trophy",
        "category": "Quizzes",
        "points": 75,
        "is_active": True,
        "rule": {
            "metric": "count",
            "event_type": "quiz_90_plus",
            "target": 5,
            "window_days": None,
        },
    },

    # 4) Streak Champion (30 day streak)
    # If you're not logging streak events, your view special-cases "streak_current".
    # You can also choose to log daily_active and compute streak differently later.
    {
        "code": "streak_champion",
        "title": "Streak Champion",
        "description": "Maintain a learning streak for consecutive days.",
        "icon": "flame",
        "category": "Consistency",
        "points": 100,
        "is_active": True,
        "rule": {
            "metric": "max",
            "event_type": "streak_current",  # view maps this to Streak.current_days
            "target": 30,
            "window_days": None,
        },
    },

    # 5) Course Conqueror (complete N courses)
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
