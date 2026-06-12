from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("campaigns", "0002_remove_campaignlead_campaigns_lead_unique_enrolment_and_more"),
        ("actions",   "0002_initial"),
    ]

    operations = [
        # Campaign: add exit_on_reply
        migrations.AddField(
            model_name="campaign",
            name="exit_on_reply",
            field=models.BooleanField(
                default=True,
                help_text="Auto-exit leads from campaign when they reply to an email",
            ),
        ),

        # CampaignLead: new statuses — update choices, add exit_reason + exited_at
        migrations.AlterField(
            model_name="campaignlead",
            name="status",
            field=models.CharField(
                choices=[
                    ("active",       "Active"),
                    ("replied",      "Replied"),
                    ("completed",    "Completed"),
                    ("exited",       "Exited"),
                    ("unsubscribed", "Unsubscribed"),
                    ("bounced",      "Bounced"),
                ],
                db_index=True,
                default="active",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="campaignlead",
            name="exit_reason",
            field=models.CharField(blank=True, max_length=200, default=""),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="campaignlead",
            name="exited_at",
            field=models.DateTimeField(blank=True, null=True),
        ),

        # CampaignSend: new statuses, open_count, reply_count, replied_at, action FK
        migrations.AlterField(
            model_name="campaignsend",
            name="status",
            field=models.CharField(
                choices=[
                    ("queued",  "Queued"),
                    ("sent",    "Sent"),
                    ("opened",  "Opened"),
                    ("replied", "Replied"),
                    ("failed",  "Failed"),
                    ("skipped", "Skipped"),
                ],
                db_index=True,
                default="queued",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="campaignsend",
            name="open_count",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="campaignsend",
            name="reply_count",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="campaignsend",
            name="replied_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="campaignsend",
            name="action",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="campaign_send",
                to="actions.action",
            ),
        ),
    ]
