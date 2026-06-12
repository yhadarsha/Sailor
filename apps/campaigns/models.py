"""
Campaigns module models.

Campaign           — a named email sequence with steps and enrolled leads
CampaignStep       — one step in the sequence (email / linkedin / task)
CampaignLead       — a lead enrolled in a campaign with individual status
CampaignSend       — one scheduled / sent email for a specific lead+step
"""

import random
import uuid
from django.db import models

# ── Docker-style name generation ──────────────────────────────────────────────
_ADJECTIVES = [
    "brave", "calm", "eager", "fast", "gentle", "happy", "kind", "lively",
    "merry", "nice", "proud", "quick", "sharp", "smart", "swift", "warm",
    "bold", "cool", "crisp", "fresh", "grand", "keen", "lean", "mighty",
    "noble", "prime", "solid", "true", "vivid", "wise",
]
_NOUNS = [
    "newton", "tesla", "curie", "darwin", "euler", "fermi", "gauss",
    "hopper", "lovelace", "turing", "volta", "watt", "bohr", "faraday",
    "hawking", "kepler", "laplace", "maxwell", "planck", "rutherford",
    "sagan", "morse", "pascal", "ramanujan", "dirac", "einstein",
]

def generate_campaign_name():
    return f"{random.choice(_ADJECTIVES)}-{random.choice(_NOUNS)}"


class Campaign(models.Model):
    STATUS_DRAFT     = "draft"
    STATUS_ACTIVE    = "active"
    STATUS_PAUSED    = "paused"
    STATUS_COMPLETED = "completed"
    STATUS_CHOICES = [
        (STATUS_DRAFT,     "Draft"),
        (STATUS_ACTIVE,    "Active"),
        (STATUS_PAUSED,    "Paused"),
        (STATUS_COMPLETED, "Completed"),
    ]

    id            = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name          = models.CharField(max_length=200, default=generate_campaign_name)
    description   = models.TextField(blank=True)
    goal          = models.CharField(max_length=200, blank=True)
    status        = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT, db_index=True
    )
    exit_on_reply = models.BooleanField(
        default=True,
        help_text="Auto-exit leads from campaign when they reply to an email"
    )
    created_by    = models.ForeignKey(
        "users.User", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="campaigns_created"
    )
    created_at    = models.DateTimeField(auto_now_add=True)
    updated_at    = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name

    def stats(self):
        sends    = CampaignSend.objects.filter(campaign_lead__campaign=self)
        sent     = sends.filter(status__in=["sent", "opened", "replied"]).count()
        opens    = sends.filter(status__in=["opened", "replied"]).count()
        replied  = self.leads.filter(status=CampaignLead.STATUS_REPLIED).count()
        enrolled = self.leads.count()
        return {
            "enrolled":   enrolled,
            "sent":       sent,
            "opens":      opens,
            "replied":    replied,
            "open_rate":  round(opens / sent * 100, 1) if sent else 0,
            "reply_rate": round(replied / enrolled * 100, 1) if enrolled else 0,
        }


class CampaignStep(models.Model):
    TYPE_EMAIL    = "email"
    TYPE_LINKEDIN = "linkedin"
    TYPE_TASK     = "task"
    TYPE_CHOICES = [
        (TYPE_EMAIL,    "Email"),
        (TYPE_LINKEDIN, "LinkedIn (manual)"),
        (TYPE_TASK,     "Task / Reminder"),
    ]

    id                 = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    campaign           = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="steps")
    step_number        = models.PositiveSmallIntegerField()
    variant_label      = models.CharField(max_length=10, default="A")
    step_type          = models.CharField(max_length=20, choices=TYPE_CHOICES, default=TYPE_EMAIL)
    label              = models.CharField(max_length=100, blank=True)
    scheduled_at       = models.DateTimeField(
        null=True, blank=True,
        help_text="Specific date/time to send. Leave blank for immediate (manual Send Now)."
    )
    subject_template   = models.CharField(max_length=300, blank=True)
    body_html_template = models.TextField(blank=True)
    task_description   = models.TextField(blank=True)
    # Attachment stored as base64 JSON list: [{name, content_type, data_b64}]
    attachments_json   = models.TextField(blank=True, default="[]")
    created_at         = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["step_number", "variant_label"]
        unique_together = [("campaign", "step_number", "variant_label")]

    def __str__(self):
        return f"{self.campaign.name} — Step {self.step_number}{self.variant_label}"


class CampaignLead(models.Model):
    STATUS_ACTIVE       = "active"
    STATUS_REPLIED      = "replied"
    STATUS_COMPLETED    = "completed"
    STATUS_EXITED       = "exited"
    STATUS_UNSUBSCRIBED = "unsubscribed"
    STATUS_BOUNCED      = "bounced"
    STATUS_CHOICES = [
        (STATUS_ACTIVE,       "Active"),
        (STATUS_REPLIED,      "Replied"),
        (STATUS_COMPLETED,    "Completed"),
        (STATUS_EXITED,       "Exited"),
        (STATUS_UNSUBSCRIBED, "Unsubscribed"),
        (STATUS_BOUNCED,      "Bounced"),
    ]

    id           = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    campaign     = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="leads")
    lead         = models.ForeignKey(
        "leads.Lead", on_delete=models.CASCADE, related_name="campaign_enrollments"
    )
    enrolled_at  = models.DateTimeField(auto_now_add=True)
    enrolled_by  = models.ForeignKey(
        "users.User", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="campaign_enrollments_made"
    )
    status       = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_ACTIVE, db_index=True
    )
    current_step = models.PositiveSmallIntegerField(default=0)
    exit_reason  = models.CharField(max_length=200, blank=True)
    exited_at    = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = [("campaign", "lead")]
        ordering = ["-enrolled_at"]

    def __str__(self):
        return f"{self.lead} → {self.campaign.name}"


class CampaignSend(models.Model):
    STATUS_QUEUED  = "queued"
    STATUS_SENT    = "sent"
    STATUS_OPENED  = "opened"
    STATUS_REPLIED = "replied"
    STATUS_FAILED  = "failed"
    STATUS_SKIPPED = "skipped"
    STATUS_BOUNCED = "bounced"
    STATUS_CHOICES = [
        (STATUS_QUEUED,  "Queued"),
        (STATUS_SENT,    "Sent"),
        (STATUS_OPENED,  "Opened"),
        (STATUS_REPLIED, "Replied"),
        (STATUS_FAILED,  "Failed"),
        (STATUS_SKIPPED, "Skipped"),
        (STATUS_BOUNCED, "Bounced"),
    ]

    id            = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    campaign_lead = models.ForeignKey(CampaignLead, on_delete=models.CASCADE, related_name="sends")
    step          = models.ForeignKey(CampaignStep, on_delete=models.CASCADE, related_name="sends")
    variant_label = models.CharField(max_length=10, default="A")
    status        = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_QUEUED, db_index=True
    )
    scheduled_for = models.DateTimeField(db_index=True)
    sent_at       = models.DateTimeField(null=True, blank=True)
    opened_at     = models.DateTimeField(null=True, blank=True)
    replied_at    = models.DateTimeField(null=True, blank=True)
    open_count    = models.PositiveSmallIntegerField(default=0)
    reply_count   = models.PositiveSmallIntegerField(default=0)
    error_message = models.TextField(blank=True)
    action        = models.OneToOneField(
        "actions.Action", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="campaign_send"
    )
    created_at    = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["scheduled_for"]

    def __str__(self):
        return f"Send {self.id} — {self.campaign_lead.lead} step {self.step.step_number}"


# ── EmailTemplate ─────────────────────────────────────────────────────────────

class EmailTemplate(models.Model):
    """Reusable email templates for campaigns and individual sends."""

    id         = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name       = models.CharField(max_length=100, help_text="Short label shown in template picker")
    subject    = models.CharField(max_length=300)
    body       = models.TextField(help_text="Plain text or simple HTML. Use {{first_name}}, {{company}}, {{sender_name}}.")
    created_by = models.ForeignKey(
        "users.User", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="email_templates"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name
