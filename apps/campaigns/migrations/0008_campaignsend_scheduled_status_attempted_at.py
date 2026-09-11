# Generated for: dispatcher refactor (non-blocking send scheduling) +
# attempted_at field used to evaluate consecutive-failure streaks in actual
# send order (independent of created_at/scheduled_for).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('campaigns', '0007_remove_campaignstep_wait_days_alter_campaign_goal_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='campaignsend',
            name='attempted_at',
            field=models.DateTimeField(
                blank=True, null=True,
                help_text="When a send was actually attempted (success or failure) — "
                           "distinct from scheduled_for/created_at, used to evaluate "
                           "consecutive-failure streaks in send order.",
            ),
        ),
        migrations.AlterField(
            model_name='campaignsend',
            name='status',
            field=models.CharField(
                choices=[
                    ('queued', 'Queued'),
                    ('scheduled', 'Scheduled'),
                    ('sent', 'Sent'),
                    ('opened', 'Opened'),
                    ('replied', 'Replied'),
                    ('failed', 'Failed'),
                    ('skipped', 'Skipped'),
                    ('bounced', 'Bounced'),
                ],
                db_index=True, default='queued', max_length=20,
            ),
        ),
    ]
