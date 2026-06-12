import uuid
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("campaigns", "0004_campaignstep_scheduled_at"),
        ("users",     "0004_allowedlogin"),
    ]

    operations = [
        migrations.CreateModel(
            name="EmailTemplate",
            fields=[
                ("id",         models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("name",       models.CharField(max_length=100)),
                ("subject",    models.CharField(max_length=300)),
                ("body",       models.TextField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("created_by", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="email_templates",
                    to="users.user",
                )),
            ],
            options={"ordering": ["name"]},
        ),
    ]
