from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q

from orgs.models import Organization
from gamification.models import Badge


DEFAULT_BADGES = [
    # Keep these thresholds aligned with your points economy.
    {
        "name": "Bronze",
        "points": 100,
        "criteria": "Reach 100 points",
        "icon_name": "medal",
        "color": "bg-amber-600",
        "rules": {},
    },
    {
        "name": "Silver",
        "points": 250,
        "criteria": "Reach 250 points",
        "icon_name": "medal",
        "color": "bg-gray-400",
        "rules": {},
    },
    {
        "name": "Gold",
        "points": 500,
        "criteria": "Reach 500 points",
        "icon_name": "trophy",
        "color": "bg-yellow-500",
        "rules": {},
    },
    {
        "name": "Platinum",
        "points": 1000,
        "criteria": "Reach 1,000 points",
        "icon_name": "crown",
        "color": "bg-indigo-500",
        "rules": {},
    },
    {
        "name": "Diamond",
        "points": 2000,
        "criteria": "Reach 2,000 points",
        "icon_name": "gem",
        "color": "bg-cyan-500",
        "rules": {},
    },
]


class Command(BaseCommand):
    help = "Seed (create/update) Badge rows from DEFAULT_BADGES."

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
            help="Seed these badges for every organization (organization=org).",
        )
        parser.add_argument(
            "--deactivate-missing",
            action="store_true",
            help="If set, any existing badges not in DEFAULT_BADGES for the scope will be deactivated (rules-only via criteria tag).",
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
            # GLOBAL scope
            target_orgs = [None]

        def upsert_one(org, payload):
            name = (payload.get("name") or "").strip()
            if not name:
                return

            data = {
                "icon_name": payload.get("icon_name") or "medal",
                "color": payload.get("color") or "bg-gray-400",
                "points": int(payload.get("points", 0) or 0),
                "criteria": payload.get("criteria") or "",
                "rules": payload.get("rules") or {},
            }

            scope_label = f"org={org.id}" if org else "GLOBAL"

            if dry_run:
                exists = Badge.objects.filter(organization=org, name=name).exists()
                action = "UPDATE" if exists else "CREATE"
                self.stdout.write(f"[DRY-RUN] {action} {scope_label} name={name} data={data}")
                return

            obj, created = Badge.objects.update_or_create(
                organization=org,
                name=name,
                defaults=data,
            )
            action = "Created" if created else "Updated"
            self.stdout.write(self.style.SUCCESS(f"{action} {scope_label} -> {obj.name} ({obj.points} pts)"))

        for org in target_orgs:
            for payload in DEFAULT_BADGES:
                upsert_one(org, payload)

            # Optional: deactivate badges not in seed list.
            # Since your Badge model doesn't have is_active, we can't flip a flag.
            # Two safe options:
            #   - add is_active to Badge; OR
            #   - mark "deprecated" in criteria or rules.
            # Here we implement the second option by tagging rules={"deprecated": true}.
            if deactivate_missing:
                seed_names = [b["name"] for b in DEFAULT_BADGES]
                qs = Badge.objects.filter(organization=org).exclude(name__in=seed_names)

                if dry_run:
                    self.stdout.write(
                        f"[DRY-RUN] Would mark deprecated={qs.count()} badges for scope {org.id if org else 'GLOBAL'}"
                    )
                else:
                    updated = 0
                    for badge in qs.iterator():
                        rules = badge.rules or {}
                        if rules.get("deprecated") is True:
                            continue
                        rules["deprecated"] = True
                        badge.rules = rules
                        badge.save(update_fields=["rules"])
                        updated += 1
                    self.stdout.write(self.style.WARNING(
                        f"Marked deprecated=True on {updated} badges for scope {org.id if org else 'GLOBAL'}"
                    ))

        self.stdout.write(self.style.SUCCESS("Seeding Badge complete."))
