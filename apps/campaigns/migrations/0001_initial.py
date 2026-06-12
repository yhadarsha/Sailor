import uuid
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("leads", "0003_lead_company_new_fields"),
        ("users", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="Campaign",
            fields=[
                ("id",          models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("name",        models.CharField(max_length=200)),
                ("description", models.TextField(blank=True)),
                ("goal",        models.CharField(blank=True, help_text="e.g. Book a demo call", max_length=200)),
                ("status",      models.CharField(
                    choices=[("draft","Draft"),("active","Active"),("paused","Paused"),("completed","Completed")],
                    db_index=True, default="draft", max_length=20,
                )),
                ("created_at",  models.DateTimeField(auto_now_add=True)),
                ("updated_at",  models.DateTimeField(auto_now=True)),
                ("created_by",  models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="campaigns_created",
                    to="users.user",
                )),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="CampaignStep",
            fields=[
                ("id",                   models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("step_number",          models.PositiveSmallIntegerField(help_text="Order of this step (1-based).")),
                ("variant_label",        models.CharField(default="A", help_text="A/B label", max_length=10)),
                ("step_type",            models.CharField(
                    choices=[("email","Email"),("linkedin","LinkedIn (manual)"),("task","Task")],
                    default="email", max_length=20,
                )),
                ("label",                models.CharField(blank=True, max_length=100)),
                ("wait_days",            models.PositiveSmallIntegerField(default=0)),
                ("subject_template",     models.CharField(blank=True, max_length=300)),
                ("body_html_template",   models.TextField(blank=True)),
                ("task_description",     models.TextField(blank=True)),
                ("created_at",           models.DateTimeField(auto_now_add=True)),
                ("campaign",             models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="steps", to="campaigns.campaign",
                )),
            ],
            options={"ordering": ["step_number", "variant_label"]},
        ),
        migrations.AddConstraint(
            model_name="campaignstep",
            constraint=models.UniqueConstraint(
                fields=["campaign", "step_number", "variant_label"],
                name="campaigns_step_unique_variant",
            ),
        ),
        migrations.CreateModel(
            name="CampaignLead",
            fields=[
                ("id",           models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("enrolled_at",  models.DateTimeField(auto_now_add=True)),
                ("status",       models.CharField(
                    choices=[("pending","Pending"),("active","Active"),("completed","Completed"),
                             ("unsubscribed","Unsubscribed"),("bounced","Bounced")],
                    db_index=True, default="pending", max_length=20,
                )),
                ("current_step", models.PositiveSmallIntegerField(default=0)),
                ("campaign",     models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="leads", to="campaigns.campaign",
                )),
                ("enrolled_by",  models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="campaign_enrollments_made", to="users.user",
                )),
                ("lead",         models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="campaign_enrollments", to="leads.lead",
                )),
            ],
            options={"ordering": ["-enrolled_at"]},
        ),
        migrations.AddConstraint(
            model_name="campaignlead",
            constraint=models.UniqueConstraint(
                fields=["campaign", "lead"],
                name="campaigns_lead_unique_enrolment",
            ),
        ),
        migrations.CreateModel(
            name="CampaignSend",
            fields=[
                ("id",            models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("variant_label", models.CharField(default="A", max_length=10)),
                ("status",        models.CharField(
                    choices=[("queued","Queued"),("sent","Sent"),("opened","Opened"),
                             ("failed","Failed"),("skipped","Skipped")],
                    db_index=True, default="queued", max_length=20,
                )),
                ("scheduled_for", models.DateTimeField(db_index=True)),
                ("sent_at",       models.DateTimeField(blank=True, null=True)),
                ("opened_at",     models.DateTimeField(blank=True, null=True)),
                ("error_message", models.TextField(blank=True)),
                ("created_at",    models.DateTimeField(auto_now_add=True)),
                ("campaign_lead", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="sends", to="campaigns.campaignlead",
                )),
                ("step",          models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="sends", to="campaigns.campaignstep",
                )),
            ],
            options={"ordering": ["scheduled_for"]},
        ),
    ]
