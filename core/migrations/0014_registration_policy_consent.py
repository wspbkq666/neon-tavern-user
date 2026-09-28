from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0013_marketlisting_bundle_kind")]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="policy_consent_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="privacy_policy_version",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="usage_rules_version",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
    ]
