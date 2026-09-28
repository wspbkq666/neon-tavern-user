from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0012_material_market")]

    operations = [
        migrations.AlterField(
            model_name="marketlisting",
            name="kind",
            field=models.CharField(
                choices=[("character", "角色卡"), ("worldbook", "世界书"), ("bundle", "整合包")],
                max_length=16,
            ),
        ),
    ]
