from django.core.management.base import BaseCommand
from accounts.models import User


class Command(BaseCommand):
    help = "Populate missing usernames for existing users"

    def generate_unique_username(self, email, user_id=None):
        base_username = (email.split("@")[0] if email else "user").strip()
        base_username = base_username[:150] or "user"

        username = base_username
        counter = 1

        qs = User.objects.all()
        if user_id:
            qs = qs.exclude(pk=user_id)

        while qs.filter(username=username).exists():
            suffix = str(counter)
            username = f"{base_username[:150 - len(suffix)]}{suffix}"
            counter += 1

        return username

    def handle(self, *args, **kwargs):
        updated = 0

        users = User.objects.filter(username__isnull=True)

        for user in users:
            username = self.generate_unique_username(user.email, user.id)
            user.username = username
            user.save(update_fields=["username"])
            updated += 1

        self.stdout.write(
            self.style.SUCCESS(f"Successfully updated {updated} users.")
        )