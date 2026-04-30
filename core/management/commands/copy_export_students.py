# academics/management/commands/export_students.py

from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model

from orgs.models import Organization
from academics.models import StudentProfile


User = get_user_model()


class Command(BaseCommand):
    help = "Print all students for a school in CSV format"

    def add_arguments(self, parser):
        parser.add_argument(
            "--org",
            type=int,
            required=True,
            help="Organization PK",
        )

    def handle(self, *args, **options):
        org_pk = options["org"]

        try:
            organization = Organization.objects.get(pk=org_pk)
        except Organization.DoesNotExist:
            raise CommandError(
                f"Organization with pk={org_pk} does not exist."
            )

        students = (
            StudentProfile.objects
            .select_related("user")
            .filter(organization=organization)
            .order_by("user__first_name", "user__last_name")
        )

        if not students.exists():
            self.stdout.write(
                self.style.WARNING("No students found.")
            )
            return

        # CSV HEADER
        self.stdout.write(
            "FULL NAME,EMAIL,ADMISSION NUMBER"
        )

        for student in students:
            user = student.user

            full_name = (
                f"{user.first_name} {user.last_name}"
            ).strip()

            email = user.email or ""

            admission_no = getattr(
                student,
                "admission_no",
                ""
            )

            self.stdout.write(
                f'"{full_name}","{email}","{admission_no}"'
            )

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"Exported {students.count()} students."
            )
        )