from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("imports", "0004_alter_leadfieldconfig_detect_keywords_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="importbatch",
            name="batch_name",
            field=models.CharField(
                max_length=200,
                blank=True,
                default="",
                help_text="User-friendly label for this import/add batch. e.g. 'Apollo Export June 2026'",
            ),
        ),
    ]
