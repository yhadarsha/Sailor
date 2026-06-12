"""
Leads app — core domain.

Contains:
  - LeadSource       : where leads come from (lookup, admin-managed)
  - RoutingZone      : geographic routing rules — NO hardcoded cities
  - Company          : normalised company entity (deduplication anchor)
  - Lead             : central lead record
  - LeadDuplicate    : deduplication resolution tracking
"""

import uuid
from django.db import models
from django.utils.text import slugify

from apps.core.models import BaseModel, SoftDeleteModel


# ── Lead Source ───────────────────────────────────────────────────────────────

class LeadSource(BaseModel):
    """
    Lookup table: where a lead was sourced from.
    Examples: LinkedIn Searches, Apollo, DataLyzer, QCFI, Industrial Hubs.
    Managed via Django admin — no code changes needed to add a new source.
    """

    class SourceType(models.TextChoices):
        DATABASE = "database", "Database / List"
        MANUAL = "manual", "Manual Entry"
        INTEGRATION = "integration", "API Integration"

    name = models.CharField(max_length=100, unique=True)
    source_type = models.CharField(max_length=20, choices=SourceType.choices)
    # Stores API credentials / config for Phase 2 integrations (Apollo API, etc.)
    integration_config = models.JSONField(
        default=dict,
        blank=True,
        help_text="Phase 2: API config for automated ingestion. Leave empty for manual sources.",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "lead_sources"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


# ── Routing Zone ──────────────────────────────────────────────────────────────

class RoutingZone(BaseModel):
    """
    Defines geographic routing rules for outreach channel selection.

    DESIGN DECISION: No 'is_bangalore' column on Lead.
    Instead, the city field on Lead is matched at runtime against city_patterns here.
    To add Chennai routing: add a RoutingZone row via admin. Zero code changes.

    city_patterns: case-insensitive list of substrings.
      Example: ["bangalore", "bengaluru", "blr"] will all match.
    priority: if a city matches multiple zones, highest priority wins.
    """

    class Action(models.TextChoices):
        PHYSICAL_POST = "physical_post", "Physical Post"
        EMAIL_CAMPAIGN = "email_campaign", "Email Campaign"
        BOTH = "both", "Both Channels"

    name = models.CharField(max_length=100, unique=True)
    city_patterns = models.JSONField(
        default=list,
        help_text='List of city name substrings to match. Example: ["bangalore", "bengaluru"]',
    )
    action = models.CharField(max_length=20, choices=Action.choices)
    priority = models.PositiveIntegerField(
        default=0,
        help_text="Higher number = higher priority when multiple zones match.",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "routing_zones"
        ordering = ["-priority", "name"]

    def __str__(self) -> str:
        return f"{self.name} → {self.get_action_display()}"

    def matches_city(self, city: str) -> bool:
        """Returns True if city matches any pattern in this zone."""
        if not city:
            return False
        city_lower = city.lower().strip()
        return any(p.lower() in city_lower for p in self.city_patterns)

    @classmethod
    def resolve_for_city(cls, city: str) -> "RoutingZone | None":
        """Returns the highest-priority active zone for a given city string."""
        for zone in cls.objects.filter(is_active=True).order_by("-priority"):
            if zone.matches_city(city):
                return zone
        return None


# ── Company ───────────────────────────────────────────────────────────────────

class Company(SoftDeleteModel):
    """
    Normalised company entity.
    Unique on (normalized_name, city) to prevent duplicates while allowing
    same-named companies in different cities ("Tata, Mumbai" vs "Tata, Delhi").
    """

    name = models.CharField(max_length=255)
    normalized_name = models.CharField(
        max_length=255,
        db_index=True,
        help_text="Auto-populated from name. Used for dedup matching.",
    )
    email_name = models.CharField(
        max_length=255,
        blank=True,
        help_text="Company name used in email templates / sender signatures.",
    )
    industry = models.CharField(max_length=150, blank=True)
    address = models.CharField(max_length=500, blank=True)
    city = models.CharField(max_length=100, blank=True, db_index=True)
    state = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=100, blank=True, default="India")
    phone = models.CharField(max_length=30, blank=True)
    website = models.URLField(blank=True)
    linkedin_url = models.URLField(blank=True)
    employee_count = models.PositiveIntegerField(null=True, blank=True)
    # AI enrichment / extra data from imports lives here
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "companies"
        ordering = ["name"]
        constraints = [
            # Unique active company per (normalized_name, city)
            models.UniqueConstraint(
                fields=["normalized_name", "city"],
                condition=models.Q(deleted_at__isnull=True),
                name="unique_company_name_city_active",
            )
        ]

    def save(self, *args, **kwargs):
        # Always derive normalized_name from name — never set manually
        self.normalized_name = self.name.lower().strip()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.name}" + (f", {self.city}" if self.city else "")


# ── Lead ──────────────────────────────────────────────────────────────────────

class Lead(SoftDeleteModel):
    """
    Central entity. Every lead lives here.

    Design notes:
    - current_stage is a DENORMALIZED CACHE of the latest LeadStageHistory row.
      It is updated via a Django signal (see pipeline/signals.py), not directly.
      Source of truth is always LeadStageHistory.
    - embedding (pgvector) is added via a custom migration after pgvector is enabled.
      See: apps/leads/migrations/0002_lead_embedding.py
    - raw_import_data preserves the original import row for auditing and re-processing.
    - organization_id seeds future multi-tenancy with zero migration cost.
    """

    class Status(models.TextChoices):
        NEW = "new", "New"
        CONTACTED = "contacted", "Contacted"
        ENGAGED = "engaged", "Engaged"
        FOLLOW_UP = "follow_up", "Follow-up"
        QUALIFIED = "qualified", "Qualified"
        DEAD = "dead", "Dead"
        BOUNCED = "bounced", "Bounced / Invalid"
        CONVERTED = "converted", "Converted"

    # ── Identity ──────────────────────────────────────────────────────────────
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100, blank=True)
    email = models.EmailField(blank=True, db_index=True)
    email_verified = models.BooleanField(default=False)
    email_bounced = models.BooleanField(default=False)
    phone = models.CharField(max_length=30, blank=True)
    linkedin_url = models.URLField(blank=True)
    linkedin_id = models.CharField(
        max_length=100,
        blank=True,
        db_index=True,
        help_text="LinkedIn numeric or username ID. Used for dedup.",
    )
    title = models.CharField(max_length=200, blank=True)
    department = models.CharField(max_length=150, blank=True)
    sub_department = models.CharField(max_length=150, blank=True)
    corporate_phone = models.CharField(max_length=30, blank=True)
    website = models.URLField(blank=True)
    facebook_url = models.URLField(blank=True)
    twitter_url = models.URLField(blank=True)

    # ── Company ───────────────────────────────────────────────────────────────
    company = models.ForeignKey(
        "Company",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="leads",
    )

    # ── Geography — raw data only, routing handled by RoutingZone ─────────────
    city = models.CharField(max_length=100, blank=True, db_index=True)
    state = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=100, blank=True, default="India")

    # ── Origin ────────────────────────────────────────────────────────────────
    source = models.ForeignKey(
        "LeadSource",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="leads",
    )
    import_batch = models.ForeignKey(
        "imports.ImportBatch",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="leads",
    )

    # ── Pipeline (denormalized cache — DO NOT update directly) ────────────────
    current_stage = models.ForeignKey(
        "pipeline.PipelineStage",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="current_leads",
        help_text="Denormalized cache. Updated automatically by pipeline signal.",
    )

    # ── Ownership ─────────────────────────────────────────────────────────────
    assigned_to = models.ForeignKey(
        "users.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assigned_leads",
    )

    # ── AI fields ─────────────────────────────────────────────────────────────
    ai_score = models.FloatField(
        null=True,
        blank=True,
        help_text="Latest AI lead score (0-100). Sourced from AILeadScore history.",
    )
    # embedding VectorField(dimensions=1536) is added via migration 0002
    # after running: CREATE EXTENSION IF NOT EXISTS vector;

    # ── Raw data ──────────────────────────────────────────────────────────────
    raw_import_data = models.JSONField(
        default=dict,
        blank=True,
        help_text="Original row from import file. Never modified after insert.",
    )

    # ── Lifecycle ─────────────────────────────────────────────────────────────
    revive_after = models.DateField(
        null=True,
        blank=True,
        help_text="Date after which a dead lead can be re-engaged (default: 90 days).",
    )
    converted_at = models.DateTimeField(null=True, blank=True)
    dead_at = models.DateTimeField(null=True, blank=True)

    # ── Multi-tenancy seed ────────────────────────────────────────────────────
    organization_id = models.UUIDField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Future multi-tenancy. Default to a single org UUID for now.",
    )

    class Meta:
        db_table = "leads"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["email"]),
            models.Index(fields=["linkedin_id"]),
            models.Index(fields=["city"]),
            models.Index(fields=["current_stage", "assigned_to"]),
            models.Index(fields=["organization_id", "current_stage"]),
        ]

    def __str__(self) -> str:
        return self.full_name or str(self.id)

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    def get_routing_zone(self) -> "RoutingZone | None":
        """Returns the best matching RoutingZone for this lead's city."""
        return RoutingZone.resolve_for_city(self.city)


# ── Lead Duplicate ────────────────────────────────────────────────────────────

class LeadDuplicate(BaseModel):
    """
    Tracks detected duplicate pairs and their resolution status.
    Unique constraint on the ordered (lead_id, duplicate_lead_id) pair
    prevents the same pair from appearing twice in reverse order.
    """

    class MatchType(models.TextChoices):
        EMAIL = "email", "Email Match"
        LINKEDIN = "linkedin", "LinkedIn ID Match"
        NAME_COMPANY = "name_company", "Name + Company Match"
        SEMANTIC = "semantic", "Semantic / AI Match"

    class Resolution(models.TextChoices):
        PENDING = "pending", "Pending Review"
        MERGED = "merged", "Merged into Primary"
        KEPT_BOTH = "kept_both", "Kept Both Records"
        IGNORED = "ignored", "Ignored / False Positive"

    lead = models.ForeignKey(
        "Lead", on_delete=models.CASCADE, related_name="duplicates"
    )
    duplicate_lead = models.ForeignKey(
        "Lead", on_delete=models.CASCADE, related_name="duplicate_of"
    )
    match_type = models.CharField(max_length=20, choices=MatchType.choices)
    confidence_score = models.FloatField(
        default=1.0,
        help_text="1.0 = exact match, <1.0 = fuzzy/AI match confidence.",
    )
    resolution = models.CharField(
        max_length=20, choices=Resolution.choices, default=Resolution.PENDING
    )
    resolved_by = models.ForeignKey(
        "users.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="resolved_duplicates",
    )
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "lead_duplicates"
        constraints = [
            models.UniqueConstraint(
                fields=["lead", "duplicate_lead"],
                name="unique_duplicate_pair",
            )
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.lead} ↔ {self.duplicate_lead} [{self.get_match_type_display()}]"
