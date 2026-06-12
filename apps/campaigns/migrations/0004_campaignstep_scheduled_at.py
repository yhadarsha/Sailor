from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("campaigns", "0003_campaign_v2_exit_reply_tracking"),
    ]

    operations = [
        migrations.AddField(
            model_name="campaignstep",
            name="scheduled_at",
            field=models.DateTimeField(
                blank=True,
                null=True,
                help_text="Specific date/time to send. Leave blank for immediate (manual Send Now).",
            ),
        ),
    ]
