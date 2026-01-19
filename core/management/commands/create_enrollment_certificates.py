from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from learning.models import Enrollment
from academics.models import EnrollmentCertificate

# Import the same number generator your endpoint uses
# Adjust this import to your actual location:
from core.utils import _gen_cert_number  # <-- change path as needed


class Command(BaseCommand):
    help = "Create EnrollmentCertificate for all completed enrollments (endpoint-like behavior)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--only-public",
            action="store_true",
            help="Only create certificates for courses with course_type='public' (optional).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Do not create anything, just report what would be created/skipped.",
        )

    def handle(self, *args, **options):
        only_public = options["only_public"]
        dry_run = options["dry_run"]

        qs = (
            Enrollment.objects
            .filter(status=Enrollment.Status.COMPLETED, course__course_type="public")
            .select_related("student", "course", "course__organization")
        )

        if only_public:
            qs = qs.filter(course__course_type="public")

        created_count = 0
        skipped_count = 0
        failed_count = 0

        for enrollment in qs.iterator():
            course = enrollment.course
            student = enrollment.student
            org = getattr(course, "organization", None)

            # If your data guarantees this, you can remove these guards.
            if not course or not student or not org:
                failed_count += 1
                self.stderr.write(
                    self.style.ERROR(
                        f"Enrollment {enrollment.id}: missing course/student/org; skipping."
                    )
                )
                continue

            # Endpoint-like duplicate protection: issued cert already exists for this enrollment
            dup_exists = EnrollmentCertificate.objects.filter(
                organization=org,
                student=student,
                enrollment=enrollment,
                status="issued",
            
            ).exists()

            if dup_exists:
                skipped_count += 1
                continue

            acquired_at = enrollment.completed_at or timezone.now()
            if acquired_at and timezone.is_naive(acquired_at):
                acquired_at = timezone.make_aware(acquired_at)

            title = f"Certificate of Completion — {course.name}"
            description = course.description

            if dry_run:
                created_count += 1
                continue

            try:
                with transaction.atomic():
                    EnrollmentCertificate.objects.create(
                        organization=org,
                        student=student,
                        enrollment=enrollment,
                        course=course,
                        number=_gen_cert_number(),
                        status="issued",
                        title=title,
                        description=description,
                        acquired_at=acquired_at,   
                    )
                created_count += 1
            except Exception as e:
                failed_count += 1
                self.stderr.write(
                    self.style.ERROR(
                        f"Failed to create certificate for enrollment {enrollment.id}: {e}"
                    )
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. Created: {created_count}, skipped: {skipped_count}, failed: {failed_count}"
                + (" (dry-run)" if dry_run else "")
            )
        )
