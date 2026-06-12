"""
Automation app — Phase 2.

Schema is defined now so no future migrations touch live lead data.
All automation rules are is_active=False until Phase 2 is implemented.

Contains:
  - Campaign        : email or physical-post campaigns targeting a lead segment
  - CampaignLead    : per-lead status within a campaign
  - AutomationRule  : trigger → condition → action rules (inactive until Phase 2)
  - WebhookLog      : execution audit trail for automation rules
"""

from django.db import models
from apps.core.models import BaseModel


class Campaign(BaseModel):
    """
    Outreach campaign targeting a subset of leads.

    target_segment stores a filter specification that can be evaluated
    to select leads at campaign launch time:
      {
        "source": ["apollo", "qcfi"],
        "city": ["chennai"],
        "current_stage": ["new", "contacted"],
        "assigned_to": ["<user-uuid>"]
      }
    """

    class Type(models.TextChoices):
        EMAIL = "email", "Email Campaign"
        POST = "post", "Physical Post"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        PAUSED = "paused", "Paused"
        DONE = "done", "Done"

    name = models.CharField(max_length=200)
    campaign_type = models.CharField(max_length=10, choices=Type.choices)
    target_segment = models.JSONField(
        default=dict,
        help_text="Filter rules used to select leads for this campaign.",
    )
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.DRAFT
    )
    created_by = models.ForeignKey(
        "users.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="campaigns",
    )
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "campaigns"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.name} [{self.get_campaign_type_display()}]"


class CampaignLead(BaseModel):
    """Per-lead status within a campaign. One row per (campaign, lead) pair."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SENT = "sent", "Sent"
        OPENED = "opened", "Opened"
        REPLIED = "replied", "Replied"
        BOUNCED = "bounced", "Bounced"

    campaign = models.ForeignKey(
        Campaign, on_delete=models.CASCADE, related_name="campaign_leads"
    )
    lead = models.ForeignKey(
        "leads.Lead", on_delete=models.CASCADE, related_name="campaigns"
    )
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING
    )
    sent_at = models.DateTimeField(null=True, blank=True)
    opened_at = models.DateTimeField(null=True, blank=True)
    outcome = models.TextField(blank=True)

    class Meta:
        db_table = "campaign_leads"
        constraints = [
            models.UniqueConstraint(
                fields=["campaign", "lead"], name="unique_campaign_lead"
            )
        ]

    def __str__(self) -> str:
        return f"{self.campaign} → {self.lead} [{self.status}]"


class AutomationRule(BaseModel):
    """
    Phase 2: Define trigger → condition → action rules.
    All rules start inactive. Activated one-by-one as automation is built out.

    trigger_event examples:
      "stage.changed"       → fires when a lead's stage changes
      "action.logged"       → fires when any action is logged
      "lead.imported"       → fires when a new lead is created via import
      "lead.revive_due"     → fires when revive_after date is reached

    conditions: JSONLogic-compatible filter (evaluated against the lead):
      {"and": [{"==": [{"var": "current_stage"}, "qualified"]}, ...]}

    action_payload: what to do when triggered:
      {"type": "webhook", "url": "https://...", "headers": {...}}
      {"type": "assign_to", "user_id": "<uuid>"}
      {"type": "send_email", "template_id": "..."}
    """

    name = models.CharField(max_length=100)
    trigger_event = models.CharField(max_length=100, db_index=True)
    conditions = models.JSONField(default=dict)
    action_payload = models.JSONField(default=dict)
    is_active = models.BooleanField(
        default=False,
        help_text="Keep False until Phase 2 automation layer is implemented.",
    )
    created_by = models.ForeignKey(
        "users.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="automation_rules",
    )

    class Meta:
        db_table = "automation_rules"

    def __str__(self) -> str:
        status = "ACTIVE" if self.is_active else "INACTIVE"
        return f"[{status}] {self.name} on {self.trigger_event}"


class WebhookLog(BaseModel):
    """
    Execution audit trail for each automation rule firing.
    Separate from AutomationRule — configuration vs. execution history.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"

    rule = models.ForeignKey(
        AutomationRule,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="logs",
    )
    lead = models.ForeignKey(
        "leads.Lead",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="webhook_logs",
    )
    webhook_url = models.URLField()
    payload = models.JSONField(default=dict)
    response_status = models.PositiveSmallIntegerField(null=True, blank=True)
    response_body = models.TextField(blank=True)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING
    )

    class Meta:
        db_table = "webhook_logs"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["rule", "status"]),
            models.Index(fields=["lead", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.rule} → {self.webhook_url} [{self.status}]"
