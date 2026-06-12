"""
Pipeline app.

Contains:
  - PipelineStage      : configurable stages (lookup, admin-managed)
  - LeadStageHistory   : append-only audit trail of every stage transition

RULE: LeadStageHistory rows are NEVER updated or deleted.
      They are the source of truth for the pipeline.
      Lead.current_stage is a read cache updated via signal (see signals.py).
"""

import uuid
from django.db import models
from apps.core.models import BaseModel


class PipelineStage(BaseModel):
    """
    Configurable pipeline stages.
    Managed via Django admin — add/rename stages without code changes.

    Seed data (create via data migration or admin):
      New → Contacted → Engaged → Follow-up → Qualified
      Dead (terminal) | Bounced/Invalid (terminal) | Converted (terminal)
    """

    name = models.CharField(max_length=100, unique=True)
    order = models.PositiveIntegerField(
        default=0,
        help_text="Display order in the pipeline board. Lower = earlier.",
    )
    color = models.CharField(
        max_length=20,
        blank=True,
        help_text="Hex color code for UI display. Example: #4ade80",
    )
    is_terminal = models.BooleanField(
        default=False,
        help_text="Terminal stages (Dead, Bounced, Converted) cannot auto-advance.",
    )
    auto_advance_rules = models.JSONField(
        default=dict,
        blank=True,
        help_text="Phase 2: Rules for automatic stage advancement. Empty = manual only.",
    )

    class Meta:
        db_table = "pipeline_stages"
        ordering = ["order"]

    def __str__(self) -> str:
        return self.name


class LeadStageHistory(models.Model):
    """
    Append-only record of every stage change for every lead.

    This table is the source of truth for:
    - How long a lead spent in each stage (time-in-stage analytics)
    - Who moved it (owner activity report)
    - Whether it was moved by a human or automation (auto_changed flag)

    Lead.current_stage is a denormalized cache of the latest row per lead.
    It is maintained by a post_save signal in pipeline/signals.py.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    lead = models.ForeignKey(
        "leads.Lead",
        on_delete=models.CASCADE,
        related_name="stage_history",
        db_index=True,
    )
    from_stage = models.ForeignKey(
        PipelineStage,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="Null on the first transition (lead just created).",
    )
    to_stage = models.ForeignKey(
        PipelineStage,
        on_delete=models.PROTECT,  # Cannot delete a stage that has history
        related_name="+",
    )
    changed_by = models.ForeignKey(
        "users.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="stage_changes",
    )
    changed_at = models.DateTimeField(auto_now_add=True, db_index=True)
    reason = models.TextField(
        blank=True,
        help_text="Optional context: why was this stage changed?",
    )
    auto_changed = models.BooleanField(
        default=False,
        help_text="True if changed by automation rule, False if changed by a human.",
    )

    class Meta:
        db_table = "lead_stage_history"
        ordering = ["changed_at"]
        indexes = [
            models.Index(fields=["lead", "changed_at"]),
            models.Index(fields=["changed_by", "changed_at"]),
        ]

    def __str__(self) -> str:
        return (
            f"{self.lead} | {self.from_stage} → {self.to_stage} @ {self.changed_at}"
        )

    def save(self, *args, **kwargs):
        # Enforce append-only: block updates to existing rows
        if self.pk and LeadStageHistory.objects.filter(pk=self.pk).exists():
            raise ValueError(
                "LeadStageHistory is append-only. Existing rows cannot be modified."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError(
            "LeadStageHistory rows cannot be deleted. They are a permanent audit trail."
        )
