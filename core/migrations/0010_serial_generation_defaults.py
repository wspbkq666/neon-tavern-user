from django.db import migrations, models


def use_serial_generation(apps, schema_editor):
    Conversation = apps.get_model("core", "Conversation")
    GenerationJob = apps.get_model("core", "GenerationJob")
    Conversation.objects.filter(auto_mode="parallel").update(auto_mode="serial")
    GenerationJob.objects.filter(status="queued", mode="parallel").update(mode="serial")


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0009_character_affinity_character_clothing_state_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="conversation",
            name="auto_mode",
            field=models.CharField(default="serial", max_length=8),
        ),
        migrations.AlterField(
            model_name="generationjob",
            name="mode",
            field=models.CharField(default="serial", max_length=8),
        ),
        migrations.RunPython(use_serial_generation, migrations.RunPython.noop),
    ]

