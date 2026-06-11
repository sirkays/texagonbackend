from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app_updates', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='appversion',
            name='download_url',
            field=models.URLField(max_length=2048),
        ),
    ]
