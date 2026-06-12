"""
Imports app.

Contains:
  - LeadFieldConfig         : DB-backed column mapping catalogue (admin-managed, zero code changes to add fields)
  - ImportBatch             : tracks every file import (status, counts, errors)
  - ColumnMappingTemplate   : saved column mappings per source (save once, reuse forever)
"""

from django.db import models
from apps.core.models import BaseModel


class LeadFieldConfig(BaseModel):
    """
    One row per mappable Lead field.
    Managed via Django admin — add new fields (Website, Industry, etc.) with zero code changes.

    field_key examples:
      "email", "first_name", "last_name", "company__name", "city"

    detect_keywords: JSON list of lowercase substrings that trigger auto-detection.
      e.g. ["email", "e-mail", "email id", "mail"]

    sort_order: controls the order in the mapping dropdown (lower = earlier).
    """

    field_key = models.CharField(
        max_length=100,
        unique=True,
        help_text="Programmatic key used in process_import(). Use '__' for related fields (e.g. company__name).",
    )
    display_label = models.CharField(
        max_length=200,
        help_text="Human-readable label shown in the mapping dropdown.",
    )
    detect_keywords = models.JSONField(
        default=list,
        blank=True,
        help_text='JSON array of lowercase substrings for auto-detection. e.g. ["email","e-mail","mail"]',
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Inactive fields are hidden from the mapping UI.",
    )
    sort_order = models.PositiveSmallIntegerField(
        default=0,
        help_text="Display order in the mapping dropdown. Lower = higher up.",
    )

    class Meta:
        db_table = "lead_field_configs"
        ordering = ["sort_order", "field_key"]

    def __str__(self) -> str:
        return f"{self.display_label} ({self.field_key})"


class ImportBatch(BaseModel):
    """
    Tracks a single file import from start to finish.

    column_mapping stores how the imported file's headers were mapped to Lead fields:
      {
        "Name": "first_name",
        "Company Name": "company__name",
        "Designation": "title",
        "City": "city",
        "Email ID": "email"
      }

    error_log stores row-level errors as a list:
      [
        {"row": 14, "error": "Invalid email format", "data": {...}},
        {"row": 27, "error": "Company name missing", "data": {...}}
      ]
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        DONE = "done", "Done"
        FAILED = "failed", "Failed"

    batch_name = models.CharField(
        max_length=200,
        blank=True,
        default="",
        help_text="User-friendly label for this import/add batch. e.g. 'Apollo Export June 2026'",
    )
    filename = models.CharField(
        max_length=255,
        help_text="Stored filename (server-side, sanitized).",
    )
    original_filename = models.CharField(
        max_length=255,
        help_text="Original filename as uploaded by the user.",
    )
    source = models.ForeignKey(
        "leads.LeadSource",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="import_batches",
    )
    uploaded_by = models.ForeignKey(
        "users.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="import_batches",
    )
    # Row counters — updated as processing completes
    total_rows = models.PositiveIntegerField(default=0)
    imported_rows = models.PositiveIntegerField(default=0)
    duplicate_rows = models.PositiveIntegerField(default=0)
    skipped_rows = models.PositiveIntegerField(default=0)

    column_mapping = models.JSONField(
        default=dict,
        help_text="Map of {source_header: lead_field} used for this import.",
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    error_log = models.JSONField(
        default=list,
        help_text="List of row-level errors encountered during processing.",
    )

    class Meta:
        db_table = "import_batches"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.original_filename} ({self.status}) — {self.imported_rows}/{self.total_rows} rows"

    @property
    def success_rate(self) -> float:
        if self.total_rows == 0:
            return 0.0
        return round((self.imported_rows / self.total_rows) * 100, 1)


class ColumnMappingTemplate(BaseModel):
    """
    Saved column mapping for a given source.
    Apollo always exports the same headers — save the mapping once, auto-apply forever.

    Example mapping:
      {
        "First Name": "first_name",
        "Last Name": "last_name",
        "Email": "email",
        "Title": "title",
        "Company": "company__name",
        "City": "city",
        "LinkedIn URL": "linkedin_url"
      }
    """

    name = models.CharField(
        max_length=100,
        help_text="Human-readable name. Example: 'Apollo Standard Export'",
    )
    source = models.ForeignKey(
        "leads.LeadSource",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="mapping_templates",
    )
    mapping = models.JSONField(
        default=dict,
        help_text="Map of {source_header: lead_field}.",
    )
    created_by = models.ForeignKey(
        "users.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="mapping_templates",
    )

    class Meta:
        db_table = "column_mapping_templates"
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name}" + (f" [{self.source}]" if self.source else "")
