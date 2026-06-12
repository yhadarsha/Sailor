"""
Migration: add new fields to Lead and Company.

Lead:
  + department       CharField(150)
  + sub_department   CharField(150)
  + corporate_phone  CharField(30)
  + website          URLField
  + facebook_url     URLField
  + twitter_url      URLField

Company:
  + email_name       CharField(255)
  + address          CharField(500)
  + phone            CharField(30)
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("leads", "0002_initial"),
    ]

    operations = [
        # ── Lead new fields ───────────────────────────────────────────────────
        migrations.AddField(
            model_name="lead",
            name="department",
            field=models.CharField(blank=True, max_length=150),
        ),
        migrations.AddField(
            model_name="lead",
            name="sub_department",
            field=models.CharField(blank=True, max_length=150),
        ),
        migrations.AddField(
            model_name="lead",
            name="corporate_phone",
            field=models.CharField(blank=True, max_length=30),
        ),
        migrations.AddField(
            model_name="lead",
            name="website",
            field=models.URLField(blank=True),
        ),
        migrations.AddField(
            model_name="lead",
            name="facebook_url",
            field=models.URLField(blank=True),
        ),
        migrations.AddField(
            model_name="lead",
            name="twitter_url",
            field=models.URLField(blank=True),
        ),
        # ── Company new fields ────────────────────────────────────────────────
        migrations.AddField(
            model_name="company",
            name="email_name",
            field=models.CharField(
                blank=True,
                max_length=255,
                help_text="Company name used in email templates / sender signatures.",
            ),
        ),
        migrations.AddField(
            model_name="company",
            name="address",
            field=models.CharField(blank=True, max_length=500),
        ),
        migrations.AddField(
            model_name="company",
            name="phone",
            field=models.CharField(blank=True, max_length=30),
        ),
    ]
