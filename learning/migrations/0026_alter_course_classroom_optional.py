from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    """
    Makes the classroom FK on Course optional (null=True, blank=True).
    The unique_together constraint is kept as-is (organization, subject, classroom, teacher)
    because PostgreSQL treats NULLs as distinct in unique indexes, so courses without
    a classroom won't conflict with each other.
    """

    dependencies = [
        ("academics", "0025_alter_teacherprofile_has_seen_onboarding"),
        ("learning", "0025_course_parent_course"),
    ]

    operations = [
        migrations.AlterField(
            model_name="course",
            name="classroom",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                to="academics.classroom",
            ),
        ),
    ]
