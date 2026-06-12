"""
AI Layer app.

Contains:
  - AILeadScore  : historical log of AI-generated lead scores
  - AIInsight    : AI-generated insights per lead (summary, next action, risk, opportunity)

Design principle:
  - Scores and insights are NEVER overwritten — new rows are appended.
    This creates a training corpus for future model improvement.
  - Lead.ai_score is a denormalized cache of the latest AILeadScore for that lead.
    Updated via signal after each new score row is inserted.
  - The embedding (pgvector) column on Lead is managed separately in leads/models.py
    and requires the pgvector PostgreSQL extension.
"""

from django.db import models
from apps.core.models import BaseModel


class AILeadScore(BaseModel):
    """
    Append-only history of AI-generated scores for each lead.

    model_version lets you track which model/prompt generated the score —
    critical for comparing model versions against each other.

    factors: breakdown of scoring signals, e.g.:
      {
        "title_match": 0.8,
        "company_size": 0.6,
        "city_match": 1.0,
        "linkedin_activity": 0.4,
        "engagement_history": 0.9
      }
    """

    lead = models.ForeignKey(
        "leads.Lead",
        on_delete=models.CASCADE,
        related_name="ai_scores",
        db_index=True,
    )
    score = models.FloatField(help_text="Score from 0.0 to 100.0")
    model_version = models.CharField(
        max_length=50,
        help_text="Model identifier. Example: 'gpt-4o-v1', 'rule-based-v2'",
    )
    factors = models.JSONField(
        default=dict,
        blank=True,
        help_text="Score breakdown by signal. Used for explainability.",
    )

    class Meta:
        db_table = "ai_lead_scores"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["lead", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.lead} | score={self.score} [{self.model_version}]"


class AIInsight(BaseModel):
    """
    AI-generated insights attached to a lead.

    insight_type controls what kind of insight this is:
      summary     → one-paragraph overview of the lead
      next_action → recommended next outreach step
      risk        → flag indicating why this lead may not convert
      opportunity → signal indicating high conversion potential

    feedback loop: users can mark insights as good/bad/ignored.
    This data is the training signal for improving future insight quality.
    """

    class InsightType(models.TextChoices):
        SUMMARY = "summary", "Lead Summary"
        NEXT_ACTION = "next_action", "Recommended Next Action"
        RISK = "risk", "Risk Flag"
        OPPORTUNITY = "opportunity", "Opportunity Signal"

    class Feedback(models.TextChoices):
        GOOD = "good", "Good"
        BAD = "bad", "Not Useful"
        IGNORED = "ignored", "Ignored"

    lead = models.ForeignKey(
        "leads.Lead",
        on_delete=models.CASCADE,
        related_name="ai_insights",
        db_index=True,
    )
    insight_type = models.CharField(max_length=20, choices=InsightType.choices)
    content = models.TextField()
    model_version = models.CharField(max_length=50)
    feedback = models.CharField(
        max_length=10,
        choices=Feedback.choices,
        blank=True,
        help_text="User feedback on this insight. Powers model improvement.",
    )

    class Meta:
        db_table = "ai_insights"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["lead", "insight_type"]),
        ]

    def __str__(self) -> str:
        return f"{self.lead} | {self.get_insight_type_display()} [{self.model_version}]"
