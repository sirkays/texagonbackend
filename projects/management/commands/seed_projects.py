from django.core.management.base import BaseCommand
from projects.seeder import seed_projects


class Command(BaseCommand):
    help = "Seed the projects app with categories, tags, and sample student projects."

    def add_arguments(self, parser):
        parser.add_argument(
            "--flush",
            action="store_true",
            help="Delete all project data before seeding.",
        )

    def handle(self, *args, **options):
        logs = seed_projects(flush=options["flush"])
        for line in logs:
            self.stdout.write(line)