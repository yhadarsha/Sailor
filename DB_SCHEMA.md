# Lead Management System — Database Schema Reference

**Database**: PostgreSQL 15+  
**Extensions required**: `pgvector` (for AI embeddings on `leads` table)  
**Django version**: 5.0  
**Last updated**: 2026-05-27

---

## Design Principles

| Principle | How it's applied |
|---|---|
| UUID PKs everywhere | No sequential integer IDs. Safe for cross-system imports, external API references, and future merges. |
| Soft deletes | `deleted_at` on `Lead` and `Company`. Records are hidden, not destroyed. GDPR = set `deleted_at`. |
| Append-only tables | `LeadStageHistory` and `Action` rows are never updated or deleted. They are permanent audit trails. |
| No hardcoded cities | `RoutingZone` table drives all geographic routing. Add Chennai, Mumbai etc. via admin — zero code changes. |
| Denormalized cache | `Lead.current_stage` is a read cache kept in sync by a Django signal. Source of truth = `LeadStageHistory`. |
| AI-native | `leads.embedding` (pgvector, 1536 dims), `AILeadScore` history, `AIInsight` with feedback loop. |
| Multi-tenancy seed | `organization_id UUID` on `Lead`. Default to one org. Multi-tenant later = add WHERE clause. |
| Phase 2 ready | `Campaign`, `CampaignLead`, `AutomationRule`, `WebhookLog` tables exist now. No future migrations needed on live data. |

---

## Domain Map

```
┌─────────────┐     ┌──────────────┐     ┌──────────────────┐
│  LeadSource │     │ RoutingZone  │     │    Company       │
│  (lookup)   │     │  (config)    │     │  (soft-delete)   │
└──────┬──────┘     └──────────────┘     └────────┬─────────┘
       │                                           │
       └──────────────────┬────────────────────────┘
                          ▼
                    ┌───────────┐
                    │   Lead    │ ◄─── ImportBatch ◄─── ColumnMappingTemplate
                    │ (central) │
                    └─────┬─────┘
          ┌───────────────┼───────────────────┐
          ▼               ▼                   ▼
  LeadStageHistory     Action           LeadDuplicate
  (append-only)     (append-only)      (dedup resolution)
          │
          └─► [signal] → Lead.current_stage (cache)

  Lead ◄──── AILeadScore (history)
  Lead ◄──── AIInsight   (feedback loop)
  Lead ◄──── CampaignLead ◄── Campaign
  Lead ◄──── WebhookLog  ◄─── AutomationRule
```

---

## Tables

### `users`

Cached Azure AD users. ID = AAD Object ID. No passwords stored.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | Azure AD Object ID (from JWT `oid` claim) |
| `email` | varchar UNIQUE | |
| `display_name` | varchar | |
| `role` | enum | `admin` / `sales` / `viewer` |
| `is_active` | bool | Set False when user leaves |
| `last_synced_at` | timestamptz | Last AAD sync time |
| `created_at` | timestamptz | |
| `updated_at` | timestamptz | |

---

### `lead_sources`

Lookup table for lead origin. Managed via admin.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `name` | varchar UNIQUE | LinkedIn Searches, Apollo, DataLyzer, QCFI, etc. |
| `source_type` | enum | `database` / `manual` / `integration` |
| `integration_config` | jsonb | Phase 2 API credentials/config |
| `is_active` | bool | |
| `created_at` | timestamptz | |
| `updated_at` | timestamptz | |

---

### `routing_zones`

Geographic routing rules. **No city names hardcoded anywhere else.**

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `name` | varchar UNIQUE | e.g. "Bangalore Zone", "Chennai Zone" |
| `city_patterns` | jsonb | `["bangalore", "bengaluru", "blr"]` — case-insensitive substring match |
| `action` | enum | `physical_post` / `email_campaign` / `both` |
| `priority` | int | Higher = wins when multiple zones match |
| `is_active` | bool | |
| `created_at` | timestamptz | |
| `updated_at` | timestamptz | |

**Logic**: `RoutingZone.resolve_for_city(lead.city)` returns the highest-priority active zone. No stored flag on Lead.

---

### `companies`

Normalised company entity. Anchors lead deduplication.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `name` | varchar | Display name |
| `normalized_name` | varchar IDX | Auto-derived: `name.lower().strip()` |
| `industry` | varchar | |
| `city` | varchar IDX | |
| `state` | varchar | |
| `country` | varchar | Default: India |
| `website` | url | |
| `linkedin_url` | url | |
| `employee_count` | int | |
| `metadata` | jsonb | AI enrichment / extra import data |
| `deleted_at` | timestamptz | Soft delete |
| `created_at` | timestamptz | |
| `updated_at` | timestamptz | |

**Unique constraint**: `(normalized_name, city)` where `deleted_at IS NULL`

---

### `leads`

Central entity. One row per person.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `first_name` | varchar | |
| `last_name` | varchar | |
| `email` | varchar IDX | |
| `email_verified` | bool | |
| `email_bounced` | bool | Set by signal when action bounce_detected=True |
| `phone` | varchar | |
| `linkedin_url` | url | |
| `linkedin_id` | varchar IDX | LinkedIn numeric or username ID |
| `title` | varchar | Job title/designation |
| `company_id` | UUID FK→companies | Nullable |
| `city` | varchar IDX | Raw city name. Routing via RoutingZone. |
| `state` | varchar | |
| `country` | varchar | Default: India |
| `source_id` | UUID FK→lead_sources | |
| `import_batch_id` | UUID FK→import_batches | |
| `current_stage_id` | UUID FK→pipeline_stages | **Read cache only** — updated by signal |
| `assigned_to_id` | UUID FK→users | Nullable (unassigned is valid) |
| `ai_score` | float | Latest score cache from AILeadScore |
| `embedding` | vector(1536) | pgvector. Added via migration 0002. HNSW index. |
| `raw_import_data` | jsonb | Original import row. Never modified. GIN index. |
| `revive_after` | date | For dead leads. Auto-set to +90 days. |
| `converted_at` | timestamptz | Set by signal on stage → Converted |
| `dead_at` | timestamptz | Set by signal on stage → Dead |
| `organization_id` | UUID IDX | Future multi-tenancy seed |
| `deleted_at` | timestamptz | Soft delete |
| `created_at` | timestamptz | |
| `updated_at` | timestamptz | |

**Indexes**: `email`, `linkedin_id`, `city`, `(current_stage, assigned_to)`, `(organization_id, current_stage)`

---

### `lead_duplicates`

Deduplication resolution tracking.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `lead_id` | UUID FK→leads | |
| `duplicate_lead_id` | UUID FK→leads | |
| `match_type` | enum | `email` / `linkedin` / `name_company` / `semantic` |
| `confidence_score` | float | 1.0 = exact match. <1.0 = fuzzy/AI confidence. |
| `resolution` | enum | `pending` / `merged` / `kept_both` / `ignored` |
| `resolved_by_id` | UUID FK→users | |
| `resolved_at` | timestamptz | |
| `created_at` | timestamptz | |
| `updated_at` | timestamptz | |

**Unique constraint**: `(lead_id, duplicate_lead_id)`

---

### `pipeline_stages`

Configurable pipeline stages. Managed via admin.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `name` | varchar UNIQUE | New, Contacted, Engaged, Follow-up, Qualified, Dead, Bounced/Invalid, Converted |
| `order` | int | Display order |
| `color` | varchar | Hex color for UI |
| `is_terminal` | bool | True for Dead, Bounced, Converted |
| `auto_advance_rules` | jsonb | Phase 2: conditions for auto-advancement |
| `created_at` | timestamptz | |
| `updated_at` | timestamptz | |

---

### `lead_stage_history`

**Append-only.** Source of truth for all stage transitions.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `lead_id` | UUID FK→leads IDX | |
| `from_stage_id` | UUID FK→pipeline_stages | Null on first entry |
| `to_stage_id` | UUID FK→pipeline_stages | |
| `changed_by_id` | UUID FK→users | |
| `changed_at` | timestamptz IDX | auto_now_add |
| `reason` | text | Why was this moved? |
| `auto_changed` | bool | True if moved by automation |

**Signal**: After INSERT → updates `Lead.current_stage` + lifecycle timestamps.

---

### `action_types`

Lookup table for action categories. Managed via admin.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `name` | varchar UNIQUE | LI Connect, LI Message, Email Sent, Cold Call, Post Sent, Note |
| `category` | enum | `linkedin` / `email` / `phone` / `physical` / `internal` |
| `requires_outcome` | bool | Cold Call = True |
| `advances_stage` | bool | Most contact actions = True |
| `icon` | varchar | UI icon identifier |
| `created_at` | timestamptz | |
| `updated_at` | timestamptz | |

---

### `actions`

**Append-only.** Every interaction with a lead.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `lead_id` | UUID FK→leads IDX | |
| `action_type_id` | UUID FK→action_types | |
| `performed_by_id` | UUID FK→users | |
| `performed_at` | timestamptz | Can be back-dated |
| `outcome` | text | Required if action_type.requires_outcome |
| `bounce_detected` | bool | Triggers Lead.email_bounced via signal |
| `dispatch_date` | date | For physical post actions |
| `metadata` | jsonb | Action-type-specific extra data |
| `created_at` | timestamptz | |

**Signal**: bounce_detected=True → sets `Lead.email_bounced = True`.

---

### `import_batches`

Tracks every file import job.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `filename` | varchar | Server-side sanitized name |
| `original_filename` | varchar | As uploaded |
| `source_id` | UUID FK→lead_sources | |
| `uploaded_by_id` | UUID FK→users | |
| `total_rows` | int | |
| `imported_rows` | int | |
| `duplicate_rows` | int | |
| `skipped_rows` | int | |
| `column_mapping` | jsonb | `{"Name": "first_name", "Email ID": "email", ...}` |
| `status` | enum | `pending` / `processing` / `done` / `failed` |
| `error_log` | jsonb | List of row-level errors |
| `created_at` | timestamptz | |
| `updated_at` | timestamptz | |

---

### `column_mapping_templates`

Saved mappings per source. Apply once, reuse forever.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `name` | varchar | e.g. "Apollo Standard Export" |
| `source_id` | UUID FK→lead_sources | |
| `mapping` | jsonb | `{"First Name": "first_name", ...}` |
| `created_by_id` | UUID FK→users | |
| `created_at` | timestamptz | |
| `updated_at` | timestamptz | |

---

### `ai_lead_scores`

Append-only AI score history.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `lead_id` | UUID FK→leads IDX | |
| `score` | float | 0.0 – 100.0 |
| `model_version` | varchar | e.g. "gpt-4o-v1", "rule-based-v2" |
| `factors` | jsonb | Score breakdown by signal |
| `created_at` | timestamptz | |
| `updated_at` | timestamptz | |

---

### `ai_insights`

AI-generated insights with user feedback loop.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `lead_id` | UUID FK→leads IDX | |
| `insight_type` | enum | `summary` / `next_action` / `risk` / `opportunity` |
| `content` | text | Generated text |
| `model_version` | varchar | |
| `feedback` | enum | `good` / `bad` / `ignored` — training signal |
| `created_at` | timestamptz | |
| `updated_at` | timestamptz | |

---

### `campaigns` (Phase 2)

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `name` | varchar | |
| `campaign_type` | enum | `email` / `post` |
| `target_segment` | jsonb | Filter rules for lead selection |
| `status` | enum | `draft` / `active` / `paused` / `done` |
| `created_by_id` | UUID FK→users | |
| `started_at` | timestamptz | |
| `ended_at` | timestamptz | |
| `metadata` | jsonb | |
| `created_at` | timestamptz | |
| `updated_at` | timestamptz | |

---

### `campaign_leads` (Phase 2)

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `campaign_id` | UUID FK→campaigns | |
| `lead_id` | UUID FK→leads | |
| `status` | enum | `pending` / `sent` / `opened` / `replied` / `bounced` |
| `sent_at` | timestamptz | |
| `opened_at` | timestamptz | |
| `outcome` | text | |

**Unique constraint**: `(campaign_id, lead_id)`

---

### `automation_rules` (Phase 2)

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `name` | varchar | |
| `trigger_event` | varchar IDX | e.g. `stage.changed`, `action.logged` |
| `conditions` | jsonb | JSONLogic filter against lead |
| `action_payload` | jsonb | What to do: webhook, assign, email |
| `is_active` | bool | Default False until Phase 2 |
| `created_by_id` | UUID FK→users | |
| `created_at` | timestamptz | |
| `updated_at` | timestamptz | |

---

### `webhook_logs` (Phase 2)

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `rule_id` | UUID FK→automation_rules | |
| `lead_id` | UUID FK→leads | |
| `webhook_url` | url | |
| `payload` | jsonb | |
| `response_status` | smallint | HTTP response code |
| `response_body` | text | |
| `status` | enum | `pending` / `success` / `failed` |
| `created_at` | timestamptz | |
| `updated_at` | timestamptz | |

---

## pgvector Setup (run once on your PostgreSQL server)

```sql
-- 1. Enable the extension
CREATE EXTENSION IF NOT EXISTS vector;

-- 2. The embedding column is added via Django migration 0002_lead_embedding.py
--    After running migrations, add the HNSW index:
CREATE INDEX leads_embedding_hnsw
ON leads
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);
```

---

## Django Management Commands (quick reference)

```bash
# Create virtual environment and install dependencies
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt

# Copy and configure environment
cp .env.example .env
# Edit .env with your DB credentials

# Run migrations
python manage.py makemigrations
python manage.py migrate

# Create superuser for Django admin
python manage.py createsuperuser

# Start development server
python manage.py runserver
```
