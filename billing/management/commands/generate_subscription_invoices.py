# texagon_academy\texagonbackend\billing\management\commands\generate_subscription_invoices.py
from django.core.management.base import BaseCommand
from django.utils import timezone

from billing.services.subscription_invoicing import generate_parent_children_subscription_invoices


class Command(BaseCommand):
    help = "Generate subscription invoices for parents' children (bulk, idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("--org-id", type=int, default=None, help="Generate for a single org")
        parser.add_argument("--dry-run", action="store_true", help="Don’t write, only show counts")

    def handle(self, *args, **opts):
        org_id = opts.get("org_id")
        dry_run = bool(opts.get("dry_run"))

        result = generate_parent_children_subscription_invoices(
          org_id=org_id,
          now=timezone.now(),
          dry_run=dry_run,
        )

        self.stdout.write(self.style.SUCCESS(
            f"parents_processed={result.parents_processed} created={result.created} skipped_existing={result.skipped_existing} dry_run={dry_run}"
        ))
