from django.core.management.base import BaseCommand
from core.models import Tier


class Command(BaseCommand):
    help = "Seed initial Tier data"

    def handle(self, *args, **options):
        tiers = [
            (0, "Newbie"),
            (2500, "Bronze Beginner"),
            (5000, "Silver Scholar"),
            (10000, "Gold Graduate"),
            (20000, "Platinum Prodigy"),
        ]

        for i, (threshold, name) in enumerate(tiers, start=1):
            tier, created = Tier.objects.update_or_create(
                threshold_xp=threshold,
                defaults={
                    "name": name,
                    "order": i,
                },
            )

            action = "Created" if created else "Updated"
            self.stdout.write(
                self.style.SUCCESS(f"{action} tier: {name} (XP ≥ {threshold})")
            )

        self.stdout.write(self.style.SUCCESS("Tier seeding completed successfully."))
