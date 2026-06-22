from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0004_allowedlogin"),
    ]

    operations = [
        migrations.CreateModel(
            name="UserMailToken",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("encrypted_cache", models.TextField(blank=True, help_text="Encrypted, serialized MSAL token cache. Never store this in plaintext.")),
                ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="mail_token", to="users.user")),
            ],
            options={
                "verbose_name": "User mail token",
                "verbose_name_plural": "User mail tokens",
                "db_table": "user_mail_tokens",
            },
        ),
    ]
