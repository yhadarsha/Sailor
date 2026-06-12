"""
Actions app — activity log.

Contains:
  - ActionType  : lookup table for action categories (admin-managed)
  - Action      : append-only record of every activity on every lead

RULE: Action rows are NEVER updated or deleted.
      They are a permanent timeline of what happened to each lead.
"""

import uuid
from django.db import models
from apps.core.models import BaseModel


class ActionType(BaseModel):
    """
    Lookup: types of actions that can be logged against a lead.
    Managed via admin — no code changes needed to add a new action type.

    Seed data:
      LI Connect     (linkedin)  advances_stage=True
      LI Message     (linkedin)  advances_stage=True
      Email Sent     (email)     advances_stage=True
      Cold Call      (phone)     advances_stage=True, requires_outcome=True
      Post Sent      (physical)  advances_stage=False
      Note           (internal)  advances_stage=False
    """

    class Category(models.TextChoices):
        LINKEDIN = "linkedin", "LinkedIn"
        EMAIL = "email", "Email"
        PHONE = "phone", "Phone / Call"
        PHYSICAL = "physical", "Physical Post"
        INTERNAL = "internal", "Internal Note"

    name = models.CharField(max_length=100, unique=True)
    category = models.CharField(max_length=20, choices=Category.choices)
    requires_outcome = models.BooleanField(
        default=False,
        help_text="If True, the outcome field must be filled when logging this action.",
    )
    advances_stage = models.BooleanField(
        default=False,
        help_text="If True, logging this action may trigger a stage advancement check.",
    )
    icon = models.CharField(
        max_length=50,
        blank=True,
        help_text="Icon identifier for UI (e.g. 'linkedin', 'mail', 'phone').",
    )

    class Meta:
        db_table = "action_types"
        ordering = ["category", "name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.get_category_display()})"


class Action(models.Model):
    """
    Append-only activity log. Every interaction with a lead is recorded here.

    Design notes:
    - performed_at is set by the user/system (not auto_now_add) to allow
      back-dating entries (e.g., logging a call that happened yesterday).
    - metadata (JSONField) stores action-type-specific data:
        Email: {"message_id": "...", "subject": "...", "opened": true}
        LinkedIn: {"connection_note": "...", "accepted": false}
        Post: {"tracking_number": "...", "courier": "BlueDart"}
    - bounce_detected triggers email_bounced=True on the parent Lead (via signal).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    lead = models.ForeignKey(
        "leads.Lead",
        on_delete=models.CASCADE,
        related_name="actions",
        db_index=True,
    )
    action_type = models.ForeignKey(
        ActionType,
        on_delete=models.PROTECT,  # Cannot delete an action type that has log entries
        related_name="actions",
    )
    performed_by = models.ForeignKey(
        "users.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="actions",
    )
    performed_at = models.DateTimeField(
        help_text="When the action actually happened (can be back-dated).",
    )
    outcome = models.TextField(
        blank=True,
        help_text="Required for action types where requires_outcome=True (e.g. Cold Call result).",
    )
    bounce_detected = models.BooleanField(
        default=False,
        help_text="Set True for email actions where a bounce was received. Syncs to Lead.email_bounced via signal.",
    )
    dispatch_date = models.DateField(
        null=True,
        blank=True,
        help_text="For physical post actions: date the package was dispatched.",
    )
    # Flexible bag-of-data for action-type-specific fields
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "actions"
        ordering = ["performed_at"]
        indexes = [
            models.Index(fields=["lead", "performed_at"]),
            models.Index(fields=["performed_by", "performed_at"]),
            models.Index(fields=["action_type", "performed_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.action_type} on {self.lead} @ {self.performed_at:%Y-%m-%d}"

    def save(self, *args, **kwargs):
        # Enforce append-only: block updates to existing rows
        if self.pk and Action.objects.filter(pk=self.pk).exists():
            raise ValueError(
                "Action is append-only. Existing action log entries cannot be modified."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError(
            "Action log entries cannot be deleted. They are a permanent audit trail."
        )
