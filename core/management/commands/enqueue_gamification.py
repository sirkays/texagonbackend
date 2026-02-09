# core/management/commands/enqueue_gamification.py
from django.core.management.base import BaseCommand
from orgs.models import Organization
from gamification.tasks import run_gamification_for_org

class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument("--org", type=int, default=None)

    def handle(self, *args, **opts):
        qs = Organization.objects.all()
        if opts["org"]:
            qs = qs.filter(id=opts["org"])

        for org in qs.iterator():
            run_gamification_for_org.delay(org.id)

        self.stdout.write(self.style.SUCCESS("Gamification tasks enqueued."))
