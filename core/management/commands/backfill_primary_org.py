"""
Django management command: backfill_primary_org

Purpose:
  For all User accounts where primary_org is NULL/empty, populate it using the
  organization from the user's StudentProfile. If a user has multiple StudentProfiles
  in different organizations, use the first one found.

Setup:
  Place this file at: your_app/management/commands/backfill_primary_org.py
  (Make sure your_app/management/__init__.py and your_app/management/commands/__init__.py exist)

Usage:
  python manage.py backfill_primary_org

Optional flags:
  --dry-run    Show how many accounts would be updated without writing to DB
  --limit N    Only process the first N accounts (useful for testing)

Examples:
  # Preview changes
  python manage.py backfill_primary_org --dry-run

  # Process only first 10 accounts
  python manage.py backfill_primary_org --limit 10

  # Actual backfill
  python manage.py backfill_primary_org

What it does:
  1. Finds all User accounts where primary_org is NULL
  2. For each such user, checks if they have a StudentProfile
  3. If yes, sets primary_org to the organization from that StudentProfile
  4. Skips users with no StudentProfile (no org to assign)
  5. Prints a summary of how many were updated
"""

from django.core.management.base import BaseCommand
from django.db import transaction
from accounts.models import User
from academics.models import StudentProfile


class Command(BaseCommand):
    help = "Backfill primary_org on User accounts from their StudentProfile organization"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be updated without actually writing to DB",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Only process the first N accounts (for testing)",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        limit = options["limit"]

        # Find all users where primary_org is NULL
        users_to_update = User.objects.filter(primary_org__isnull=True)
        if limit:
            users_to_update = users_to_update[:limit]
        else:
            users_to_update = list(users_to_update)

        total_count = len(users_to_update)
        self.stdout.write(f"\nFound {total_count} user(s) with primary_org = NULL")

        if total_count == 0:
            self.stdout.write(self.style.SUCCESS("No users to update."))
            return

        if dry_run:
            self.stdout.write(self.style.WARNING("\n--dry-run: showing preview only.\n"))

        updated_count = 0
        skipped_count = 0
        updates_to_make = []

        for user in users_to_update:
            # Try to find a StudentProfile for this user
            student_profile = StudentProfile.objects.filter(user=user).first()

            if student_profile and student_profile.organization:
                updates_to_make.append((user.id, user.email, student_profile.organization))
                updated_count += 1
                self.stdout.write(
                    f"  ✓ User {user.email} "
                    f"← org {student_profile.organization.name} (pk={student_profile.organization.pk})"
                )
            else:
                skipped_count += 1
                self.stdout.write(
                    f"  ✗ User {user.email} has no StudentProfile with organization — skipped"
                )

        if dry_run:
            self.stdout.write(self.style.WARNING(
                f"\n--dry-run summary:\n"
                f"  Would update: {updated_count} users\n"
                f"  Would skip  : {skipped_count} users\n"
            ))
            return

        # Write to DB
        if updated_count == 0:
            self.stdout.write(self.style.WARNING("\nNo users found with a StudentProfile organization — nothing to update."))
            return

        self.stdout.write(f"\nWriting {updated_count} update(s) to database...")

        with transaction.atomic():
            for user_id, user_email, org in updates_to_make:
                User.objects.filter(pk=user_id).update(primary_org=org)

        self.stdout.write(self.style.SUCCESS(
            f"\n{'='*52}\n"
            f"  Updated: {updated_count} user(s)\n"
            f"  Skipped: {skipped_count} user(s)\n"
            f"{'='*52}"
        ))