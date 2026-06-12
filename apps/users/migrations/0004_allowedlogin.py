from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0003_user_phone_userdevice"),
    ]

    operations = [
        migrations.CreateModel(
            name="AllowedLogin",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("email", models.EmailField(db_index=True, max_length=254, unique=True)),
                ("display_name", models.CharField(blank=True, max_length=255)),
                ("role", models.CharField(choices=[("admin", "Admin"), ("sales", "Sales Rep"), ("viewer", "Viewer")], default="sales", max_length=20)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
            ],
            options={
                "verbose_name": "Allowed login",
                "verbose_name_plural": "Allowed logins",
                "db_table": "allowed_logins",
                "ordering": ["email"],
            },
        ),
    ]
