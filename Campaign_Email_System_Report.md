# Sailor LMS — Campaign Email System Report

*For audit/review. Covers architecture, send flow, and anti-blocking safeguards as of 2026-06-26.*

## 1. Architecture

Sailor sends campaign emails through the **Microsoft Graph API** (`POST /me/sendMail`), authenticated with the campaign creator's own delegated OAuth token (MSAL). There is no third-party SMTP relay — every email is sent *as* the actual signed-in user from their real Outlook/M365 mailbox. Tokens are refreshed from an encrypted, persistent token store (`UserMailToken`) so sends can run unattended even if the user isn't logged in; a session-cache fallback covers users who haven't logged in since that feature shipped.

Data model: `Campaign` (status: draft/active/paused/completed) → `CampaignStep` (one email/LinkedIn/task step in a sequence) → `CampaignLead` (a lead enrolled in a campaign) → `CampaignSend` (one scheduled/sent email for a lead+step, status: queued/sent/opened/replied/failed/skipped/bounced).

## 2. Send Flow

A Celery Beat task, `process_due_campaign_sends`, runs every 5 minutes. Each run:

1. Pulls up to 20 `CampaignSend` rows that are `queued`, due (`scheduled_for <= now`), belong to an *active* campaign, and whose lead is active/completed.
2. For each: fetches the sender's Graph token, renders the subject/body template (with a tracking pixel), logs an `Action` record, and calls `send_graph_email`.
3. On success → marks `sent`, advances the lead's step. On failure → marks `failed` with the error, and updates failure bookkeeping (below).
4. Sleeps a randomized **50–70 seconds** before the next send in the batch (no fixed cadence).

## 3. Anti-Blocking Safeguards (added after the 2026-06-26 Outlook block incident)

The original incident: ~30 emails fired at a fixed 2-second cadence with no daily ceiling and no failure handling, which Microsoft's outbound-spam detection (and/or the org's outbound spam policy) flagged, restricting the mailbox mid-run.

| Safeguard | Behavior |
|---|---|
| **Jittered pacing** | Random 50–70s gap between sends — removes the bot-like fixed-interval signature. |
| **Per-mailbox daily cap** | Hard ceiling of **30 sends/24h** per user (`CAMPAIGN_MAX_SENDS_PER_USER_PER_DAY`), independent of how many are due. Excess stays queued for the next day. |
| **Warm-up ramp** | A mailbox with no send history starts at 15/day for its first day, then the full 30/day cap applies (`CAMPAIGN_WARMUP_SCHEDULE`). Ramp restarts whenever a mailbox has no recent successful sends (e.g. right after an unblock). |
| **Throttle-aware retries** | `send_graph_email` detects Graph's two throttle signatures — HTTP 429 (has `Retry-After`) and HTTP 403 `ApplicationThrottled` (no `Retry-After`, fixed 30s backoff used instead) — and retries once before giving up. |
| **Circuit breaker** | If a send is throttled, or 3 sends fail consecutively for one user in a run, all of that user's active campaigns are immediately set to `paused` and no further sends for that user happen in the run. |
| **Pause is sticky and manual-only** | The due-sends query excludes non-`active` campaigns, so a paused campaign is never picked up again automatically. There is no auto-resume, no timer, no retry-after-N-hours logic — a human must review the mailbox and manually set the campaign back to `active`. |

## 4. Configuration (Django settings, all optional overrides)

`CAMPAIGN_MIN_SEND_DELAY` / `CAMPAIGN_MAX_SEND_DELAY` (default 120/300s — 2 to 5 min random gap between sends per mailbox) · `CAMPAIGN_MAX_SENDS_PER_USER_PER_DAY` (default 30) · `CAMPAIGN_WARMUP_SCHEDULE` (default `[(1, 15)]`) · `CAMPAIGN_MAX_CONSECUTIVE_FAILURES` (default 3).

## 5. Known Limitations / Residual Risk

- **No bounce/NDR ingestion.** `CampaignLead.STATUS_BOUNCED` exists as a status but nothing currently populates it from incoming mail — hard bounces aren't auto-detected or excluded.
- **Tenant-side outbound spam policy is outside this app's control.** Microsoft 365 admins can configure stricter hourly/daily external-recipient thresholds in the Defender portal that would override anything this app does; not visible or adjustable from code.
- **No SPF/DKIM/DMARC or dedicated sending-domain setup** — this is DNS/tenant infrastructure, not application code, and affects deliverability/reputation independently of send pacing.
- **Single mailbox = single point of failure** — all campaign volume currently rides on one user's reputation; no multi-mailbox distribution.
- **Microsoft's hard technical limits remain untouched** (30 msgs/min, 10,000 recipients/day, 500 recipients/message) — the app's 30/day cap sits far under these by design, as a safety margin, not because the technical ceiling is close.
