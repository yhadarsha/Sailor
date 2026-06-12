"""
Pipeline signals.

The key signal here keeps Lead.current_stage in sync with LeadStageHistory.

Rule: Lead.current_stage is NEVER updated by application code directly.
      Only this signal updates it, after a new LeadStageHistory row is saved.
      This guarantees the cache is always consistent with the audit trail.
"""

from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from apps.pipeline.models import LeadStageHistory


@receiver(post_save, sender=LeadStageHistory)
def sync_lead_current_stage(sender, instance: LeadStageHistory, created: bool, **kwargs):
    """
    After a new stage history row is inserted:
    1. Update Lead.current_stage to the new stage.
    2. Set lifecycle timestamps (converted_at, dead_at) if moving to terminal stages.
    """
    if not created:
        # Should never happen (append-only), but guard anyway
        return

    lead = instance.lead
    to_stage = instance.to_stage

    update_fields = {"current_stage": to_stage}

    # Set lifecycle timestamps when moving to terminal stages
    stage_name_lower = to_stage.name.lower()
    if stage_name_lower == "converted":
        update_fields["converted_at"] = instance.changed_at
    elif stage_name_lower == "dead":
        update_fields["dead_at"] = instance.changed_at
        # Auto-set revive date (90 days) if not already set
        if not lead.revive_after:
            from datetime import timedelta
            update_fields["revive_after"] = instance.changed_at.date() + timedelta(days=90)

    # Use update() to avoid triggering another save signal loop
    type(lead).all_objects.filter(pk=lead.pk).update(**update_fields)
