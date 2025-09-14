# billing/management/commands/generate_parent_invoices.py
from django.core.management.base import BaseCommand
from django.utils import timezone
from academics.models import ParentProfile
from django.db import transaction

class Command(BaseCommand):
    help = "Generate due subscription invoices for ParentProfile objects."

    def handle(self, *args, **options):
        now = timezone.now()
        qs = ParentProfile.objects.select_related("organization_subscription__plan").filter(
            organization_subscription__isnull=False,
            organization_subscription__status=ParentProfile.organization_subscription.field.related_model.Status.ACTIVE
        )
        # above filter uses model attr for clarity; you can replace with literal "active"

        total_created = 0
        for parent in qs:
            try:
                created = parent.generate_subscription_invoices(now=now)
                total_created += len(created)
                if created:
                    self.stdout.write(self.style.SUCCESS(
                        f"Created {len(created)} invoice(s) for ParentProfile id={parent.id}"
                    ))
            except Exception as e:
                self.stderr.write(f"Error for parent id={parent.id}: {e}")

        self.stdout.write(self.style.SUCCESS(f"Done. Total created invoices: {total_created}"))
