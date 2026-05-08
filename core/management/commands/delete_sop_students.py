# academics/management/commands/delete_sop_students.py

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from academics.models import StudentProfile, Classroom
from billing.models import UserAccountSubscription
from orgs.models import OrganizationMembership


class Command(BaseCommand):
    help = (
        "Delete students whose User.email contains 'sop@', including their "
        "StudentProfile, UserAccountSubscription, OrganizationMembership, "
        "and current_classroom."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Actually perform the delete. Without this flag, only a dry-run summary is shown.",
        )

        parser.add_argument(
            "--email-pattern",
            default="pssigboora@",
            help="Email pattern to match. Default: sop@",
        )

        parser.add_argument(
            "--include-shared-classrooms",
            action="store_true",
            help=(
                "Also delete classrooms even if they are used by students outside "
                "the matched sop@ users. By default, shared classrooms are skipped."
            ),
        )

    def handle(self, *args, **options):
        User = get_user_model()

        confirm = options["confirm"]
        email_pattern = options["email_pattern"]
        include_shared_classrooms = options["include_shared_classrooms"]

        users_qs = User.objects.filter(email__icontains=email_pattern)
        user_ids = list(users_qs.values_list("id", flat=True))

        if not user_ids:
            self.stdout.write(
                self.style.WARNING(f"No users found with email containing {email_pattern!r}.")
            )
            return

        student_profiles_qs = StudentProfile.objects.filter(user_id__in=user_ids)
        student_profile_ids = list(student_profiles_qs.values_list("id", flat=True))

        classroom_ids = list(
            student_profiles_qs
            .exclude(current_classroom__isnull=True)
            .values_list("current_classroom_id", flat=True)
            .distinct()
        )

        if include_shared_classrooms:
            classrooms_to_delete_ids = classroom_ids
            shared_classrooms_skipped_ids = []
        else:
            shared_classrooms_skipped_ids = list(
                StudentProfile.objects
                .filter(current_classroom_id__in=classroom_ids)
                .exclude(user_id__in=user_ids)
                .values_list("current_classroom_id", flat=True)
                .distinct()
            )

            classrooms_to_delete_ids = [
                classroom_id
                for classroom_id in classroom_ids
                if classroom_id not in shared_classrooms_skipped_ids
            ]

        subscriptions_count = UserAccountSubscription.objects.filter(user_id__in=user_ids).count()
        memberships_count = OrganizationMembership.objects.filter(user_id__in=user_ids).count()
        classrooms_count = Classroom.objects.filter(id__in=classrooms_to_delete_ids).count()

        self.stdout.write("")
        self.stdout.write("Delete summary")
        self.stdout.write("--------------")
        self.stdout.write(f"Email pattern: {email_pattern!r}")
        self.stdout.write(f"Users found: {len(user_ids)}")
        self.stdout.write(f"Student profiles found: {len(student_profile_ids)}")
        self.stdout.write(f"User account subscriptions found: {subscriptions_count}")
        self.stdout.write(f"Organization memberships found: {memberships_count}")
        self.stdout.write(f"Current classrooms found: {len(classroom_ids)}")
        self.stdout.write(f"Classrooms selected for delete: {classrooms_count}")

        if shared_classrooms_skipped_ids:
            self.stdout.write(
                self.style.WARNING(
                    f"Shared classrooms skipped: {len(shared_classrooms_skipped_ids)}. "
                    "Use --include-shared-classrooms to delete them too."
                )
            )

        if not confirm:
            self.stdout.write("")
            self.stdout.write(
                self.style.WARNING(
                    "Dry run only. Re-run with --confirm to actually delete records."
                )
            )
            return

        with transaction.atomic():
            deleted_subscriptions, _ = UserAccountSubscription.objects.filter(
                user_id__in=user_ids
            ).delete()

            deleted_memberships, _ = OrganizationMembership.objects.filter(
                user_id__in=user_ids
            ).delete()

            deleted_student_profiles, _ = StudentProfile.objects.filter(
                user_id__in=user_ids
            ).delete()

            deleted_users, _ = User.objects.filter(
                id__in=user_ids
            ).delete()

            deleted_classrooms, _ = Classroom.objects.filter(
                id__in=classrooms_to_delete_ids
            ).delete()

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Deletion completed."))
        self.stdout.write(f"Deleted UserAccountSubscription rows/cascades: {deleted_subscriptions}")
        self.stdout.write(f"Deleted OrganizationMembership rows/cascades: {deleted_memberships}")
        self.stdout.write(f"Deleted StudentProfile rows/cascades: {deleted_student_profiles}")
        self.stdout.write(f"Deleted User rows/cascades: {deleted_users}")
        self.stdout.write(f"Deleted Classroom rows/cascades: {deleted_classrooms}")