# core/management/commands/enqueue_progress.py
from django.core.management.base import BaseCommand
from django.apps import apps
from learning.tasks import recalc_progress_chunk
from learning.models import Enrollment

class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument("--chunk-size", type=int, default=200)

    def handle(self, *args, **opts):
        chunk_size = opts["chunk_size"]

        ids = list(
            Enrollment.objects.exclude(status=Enrollment.Status.COMPLETED)
            .values_list("id", flat=True)
        )

        for i in range(0, len(ids), chunk_size):
            recalc_progress_chunk.delay(ids[i:i+chunk_size])

        self.stdout.write(self.style.SUCCESS(f"Enqueued {len(ids)} enrollments in chunks."))
