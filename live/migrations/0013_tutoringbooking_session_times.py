from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("live", "0012_privatetutoring_hours_per_day"),
    ]

    operations = [
        migrations.AddField(
            model_name="tutoringbooking",
            name="session_start_time",
            field=models.TimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="tutoringbooking",
            name="session_end_time",
            field=models.TimeField(blank=True, null=True),
        ),
    ]
