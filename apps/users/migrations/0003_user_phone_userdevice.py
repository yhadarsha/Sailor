from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0002_alter_user_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="auth_phone",
            field=models.CharField(
                blank=True,
                help_text="Phone number read from Microsoft authentication methods when permitted.",
                max_length=40,
            ),
        ),
        migrations.AddField(
            model_name="user",
            name="business_phone",
            field=models.CharField(blank=True, max_length=40),
        ),
        migrations.AddField(
            model_name="user",
            name="mobile_phone",
            field=models.CharField(blank=True, max_length=40),
        ),
        migrations.CreateModel(
            name="UserDevice",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(blank=True, max_length=120)),
                ("endpoint", models.TextField(unique=True)),
                ("p256dh", models.TextField()),
                ("auth", models.TextField()),
                ("user_agent", models.TextField(blank=True)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("last_seen_at", models.DateTimeField(blank=True, null=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="devices",
                        to="users.user",
                    ),
                ),
            ],
            options={
                "db_table": "user_devices",
                "ordering": ["-last_seen_at", "-created_at"],
            },
        ),
    ]
