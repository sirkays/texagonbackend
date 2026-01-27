from django.core.management.base import BaseCommand
from django.conf import settings
from orgs.models import Organization

class Command(BaseCommand):
    help = "Enqueue subscription invoice generation per org (Celery in prod; sync in staging)."

    def add_arguments(self, parser):
        parser.add_argument("--org-id", type=int, default=None)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        org_id = opts.get("org_id")
        dry_run = bool(opts.get("dry_run"))

        if dry_run:
            self.stdout.write("Dry-run: will not enqueue or generate.")
            return

        qs = Organization.objects.all()
        if org_id:
            qs = qs.filter(id=org_id)

        org_ids = list(qs.values_list("id", flat=True))

        if not org_ids:
            self.stdout.write("No organizations found.")
            return

        if getattr(settings, "USE_CELERY", False):
            from billing.tasks import generate_invoices_for_org
            for oid in org_ids:
                generate_invoices_for_org.delay(oid)
            self.stdout.write(self.style.SUCCESS(f"Enqueued billing for {len(org_ids)} org(s)."))
        else:
            # staging fallback: run synchronously
            from django.utils import timezone
            from billing.services.subscription_invoicing import generate_parent_children_subscription_invoices
            for oid in org_ids:
                generate_parent_children_subscription_invoices(org_id=oid, now=timezone.now(), dry_run=False)
            self.stdout.write(self.style.SUCCESS(f"Generated billing synchronously for {len(org_ids)} org(s)."))
