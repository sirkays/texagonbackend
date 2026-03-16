from django.core.management.base import BaseCommand
from blog.seeder import seed_blog, POSTS


class Command(BaseCommand):
    help = "Seed the blog with categories, tags, authors, posts, and newsletter subscribers."

    def add_arguments(self, parser):
        parser.add_argument("--flush", action="store_true")
        parser.add_argument("--posts", type=int, default=len(POSTS))
        parser.add_argument("--no-subscribers", action="store_true")

    def handle(self, *args, **options):
        logs = seed_blog(
            flush=options["flush"],
            posts_count=options["posts"],
            no_subscribers=options["no_subscribers"],
        )

        for line in logs:
            self.stdout.write(line)