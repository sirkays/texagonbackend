from django.core.management.base import BaseCommand
from django.utils import timezone
from academics.models import ParentChildLink
from billing.models import UserAccountSubscription  # adjust import
from decimal import Decimal

class Command(BaseCommand):
    help = "Backfill UserAccountSubscription for students from ParentProfile.organization_subscription."

    def handle(self, *args, **opts):
        now = timezone.now()
        created = 0
        updated = 0

        qs = ParentChildLink.objects.select_related(
            "parent__organization",
            "parent__organization_subscription__plan",
            "student__user",
        )

        for link in qs.iterator(chunk_size=2000):
            parent = link.parent
            student = link.student
            sub = parent.organization_subscription
            if not sub or sub.status != sub.Status.ACTIVE:
                continue

            plan = sub.plan
            if not plan:
                continue

            amount = Decimal(getattr(plan, "price", 0) or 0)

            obj, is_created = UserAccountSubscription.objects.update_or_create(
                organization=parent.organization,
                user=student.user,
                defaults={
                    "plan": plan,
                    "status": UserAccountSubscription.Status.ACTIVE,
                    "start_at": now,
                    "end_at": None,  # you can compute if you want
                    "auto_renew": True,
                    "billed_to_parent": parent,
                    "amount": amount,
                    "currency": "NGN",
                    "meta": {"source": "backfill_from_parent_profile"},
                },
            )

            if is_created:
                created += 1
            else:
                updated += 1

        self.stdout.write(self.style.SUCCESS(f"created={created} updated={updated}"))
