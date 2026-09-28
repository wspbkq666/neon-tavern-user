from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0010_serial_generation_defaults")]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="site_key",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
    ]

