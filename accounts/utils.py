from datetime import timedelta

from django.db.models import (
    Exists, OuterRef, F, ExpressionWrapper,
    DateTimeField, DurationField,
)
from django.db.models.functions import Now

from academics.models import EnrollmentCertificate, StudentEnrollmentCertificateApproval


def available_certificates_qs():
    # approvals exist (per role) and are approved=True
    teacher_approved = StudentEnrollmentCertificateApproval.objects.filter(
        certificate_id=OuterRef("pk"),
        user_type=StudentEnrollmentCertificateApproval.UserType.TEACHER,
        approval=True,
    )
    admin_approved = StudentEnrollmentCertificateApproval.objects.filter(
        certificate_id=OuterRef("pk"),
        user_type=StudentEnrollmentCertificateApproval.UserType.ADMIN,
        approval=True,
    )

    # Multiply download_after_days (integer) by timedelta(days=1) so Django
    # emits a proper SQL INTERVAL expression — compatible with PostgreSQL
    # (timestamptz + interval) and SQLite alike.
    days_as_duration = ExpressionWrapper(
        F("download_after_days") * timedelta(days=1),
        output_field=DurationField(),
    )

    # downloadable_at = acquired_at + download_after_days * 1 day
    downloadable_at = ExpressionWrapper(
        F("acquired_at") + days_as_duration,
        output_field=DateTimeField(),
    )

    return (
        EnrollmentCertificate.objects
        .filter(status=EnrollmentCertificate.Status.ISSUED)
        .annotate(
            downloadable_at_calc=downloadable_at,
            teacher_ok=Exists(teacher_approved),
            admin_ok=Exists(admin_approved),
        )
        .filter(
            downloadable_at_calc__lte=Now(),
            teacher_ok=True,
            admin_ok=True,
        )
    )
