"""
Migration: add LeadFieldConfig table + seed initial field catalogue.
"""

import uuid
from django.db import migrations, models
import django.utils.timezone


INITIAL_FIELDS = [
    # (field_key, display_label, sort_order, detect_keywords)
    ("full_name",     "Full Name (auto-split first / last)",  0,  ["full name", "name", "contact name", "lead name", "person"]),
    ("first_name",    "First Name",                           1,  ["first name", "first", "fname", "given name"]),
    ("last_name",     "Last Name",                            2,  ["last name", "last", "lname", "surname"]),
    ("email",         "Email",                                3,  ["email", "e-mail", "email id", "email address", "mail"]),
    ("phone",         "Phone / Mobile",                       4,  ["phone", "mobile", "contact no", "cell", "tel", "ph no", "mobile no"]),
    ("title",         "Job Title / Designation",              5,  ["title", "designation", "job title", "position", "role"]),
    ("linkedin_url",  "LinkedIn URL",                         6,  ["linkedin url", "linkedin", "li url", "linkedin profile"]),
    ("linkedin_id",   "LinkedIn ID",                          7,  ["linkedin id", "li id"]),
    ("company__name", "Company Name",                         8,  ["company", "company name", "organization", "organisation", "org", "employer", "firm"]),
    ("city",          "City",                                 9,  ["city", "location", "place", "town"]),
    ("state",         "State",                                10, ["state", "province", "region"]),
    ("country",       "Country",                              11, ["country"]),
]


def seed_field_configs(apps, schema_editor):
    LeadFieldConfig = apps.get_model("imports", "LeadFieldConfig")
    now = django.utils.timezone.now()
    for field_key, display_label, sort_order, keywords in INITIAL_FIELDS:
        LeadFieldConfig.objects.get_or_create(
            field_key=field_key,
            defaults={
                "id":             uuid.uuid4(),
                "display_label":  display_label,
                "sort_order":     sort_order,
                "detect_keywords": keywords,
                "is_active":      True,
                "created_at":     now,
                "updated_at":     now,
            },
        )


def remove_field_configs(apps, schema_editor):
    LeadFieldConfig = apps.get_model("imports", "LeadFieldConfig")
    LeadFieldConfig.objects.filter(
        field_key__in=[f[0] for f in INITIAL_FIELDS]
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("imports", "0002_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="LeadFieldConfig",
            fields=[
                ("id",            models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("created_at",    models.DateTimeField(auto_now_add=True)),
                ("updated_at",    models.DateTimeField(auto_now=True)),
                ("field_key",     models.CharField(max_length=100, unique=True,
                                  help_text="Programmatic key. Use __ for related fields.")),
                ("display_label", models.CharField(max_length=200,
                                  help_text="Human-readable label in the mapping dropdown.")),
                ("detect_keywords", models.JSONField(default=list, blank=True,
                                   help_text='JSON array of lowercase auto-detection keywords.')),
                ("is_active",     models.BooleanField(default=True,
                                  help_text="Inactive fields are hidden from the mapping UI.")),
                ("sort_order",    models.PositiveSmallIntegerField(default=0,
                                  help_text="Display order. Lower = higher up.")),
            ],
            options={"db_table": "lead_field_configs", "ordering": ["sort_order", "field_key"]},
        ),
        migrations.RunPython(seed_field_configs, remove_field_configs),
    ]
