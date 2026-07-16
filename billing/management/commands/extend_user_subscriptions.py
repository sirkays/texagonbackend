from django.core.management.base import BaseCommand
from dateutil.relativedelta import relativedelta
from billing.models import UserAccountSubscription

class Command(BaseCommand):
    help = "Extends the end_at field for all UserAccountSubscription instances by a specified number of months (default: 4)."

    def add_arguments(self, parser):
        parser.add_argument(
            '--months',
            type=int,
            default=4,
            help='Number of months to extend the subscriptions by (default is 4)'
        )

    def handle(self, *args, **options):
        months = options['months']
        subscriptions = UserAccountSubscription.objects.all()
        updated_count = 0
        skipped_count = 0

        for sub in subscriptions.iterator(chunk_size=1000):
            if sub.end_at:
                sub.end_at = sub.end_at + relativedelta(months=months)
                sub.refresh_status(save=False)
                sub.save(update_fields=['end_at', 'status', 'updated_at'])
                updated_count += 1
            else:
                # If end_at is None, it could be a lifetime subscription or something similar.
                # We skip it.
                skipped_count += 1

        self.stdout.write(self.style.SUCCESS(
            f'Successfully extended {updated_count} subscriptions by {months} months. '
            f'Skipped {skipped_count} subscriptions without an end_at.'
        ))
