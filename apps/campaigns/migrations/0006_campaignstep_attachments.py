from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("campaigns", "0005_emailtemplate"),
    ]

    operations = [
        migrations.AddField(
            model_name="campaignstep",
            name="attachments_json",
            field=models.TextField(blank=True, default="[]"),
        ),
    ]
