---
name: sailor-leads-api
description: Fetch and analyze leads/pipeline data from DataLyzer's Sailor LMS via its read-only JSON API. Use when asked about leads, pipeline stages, lead counts, or data from Sailor/sailor.datalyzerint.com.
---

# Sailor Leads API

Sailor is DataLyzer's custom-built Lead Management System (a Django app), hosted at `https://sailor.datalyzerint.com`. It exposes a small, read-only JSON API for pulling lead records — no database access or browser login required.

## Prerequisites

- The domain `sailor.datalyzerint.com` must be on this account's network egress allowlist (Admin settings → Capabilities in the Claude admin console). If a request to it fails with a connection/proxy error rather than a normal HTTP response, that's the first thing to check with the user.

## Authentication

Every request needs this header:

```
X-API-Key: hKLGWjAuPurTkdX-gO1bQnqERDLKsBptRYwthIT6t4A
```

Treat this key like a password — it grants read access to all lead data, including names, emails, and phone numbers. Never paste it anywhere public, and don't include it in artifacts, shared documents, or any output the user hasn't explicitly asked to include it in.

## Endpoints

### List leads

```
GET https://sailor.datalyzerint.com/api/v1/leads/
```

Query params (all optional, combine freely):

- `city` — case-insensitive substring match on the lead's city
- `batch` — import batch UUID (exact match)
- `stage` — pipeline stage name, case-insensitive exact match (e.g. "New", "Contacted", "Engaged", "Follow-up", "Qualified", "Dead", "Bounced", "Converted")
- `source` — lead source name, case-insensitive exact match (e.g. "LinkedIn Searches", "Apollo", "DataLyzer")
- `q` — free-text search across first name, last name, email, and company name
- `updated_since` — ISO-8601 datetime; only leads updated at or after this
- `page` — page number, default 1
- `page_size` — results per page, default 50, max 200

Response shape:

```json
{
  "count": 804,
  "page": 1,
  "num_pages": 17,
  "page_size": 50,
  "results": [ { "...": "lead object, see below" } ]
}
```

Each lead object includes: `id`, `first_name`, `last_name`, `full_name`, `email`, `email_verified`, `email_bounced`, `phone`, `title`, `department`, `sub_department`, `linkedin_url`, `city`, `state`, `country`, `company` (`id`, `name`, `industry`, `city`, `website`, or `null`), `source` (name or `null`), `import_batch` (batch name or `null`), `current_stage` (stage name or `null`), `assigned_to` (`id`, `name`, `email`, or `null`), `ai_score`, `converted_at`, `dead_at`, `created_at`, `updated_at`.

### Single lead

```
GET https://sailor.datalyzerint.com/api/v1/leads/<uuid>/
```

Returns one lead object as above, or a 404 JSON error if the id doesn't exist.

## Usage notes

- There are 800+ leads in the system — never assume one page has everything. Check `count` / `num_pages` in the response and fetch additional pages (raise `page_size` up to 200, or loop `page`) when the user's question needs the full set rather than a sample.
- Call it with `curl` (via the Bash/shell tool) or a fetch-style tool, setting the `X-API-Key` header manually — there is no dedicated connector for this, it's a plain authenticated REST call over HTTPS.
- A `401` JSON response means the API key is missing or wrong. A connection failure with no HTTP status at all (not even a 4xx/5xx) almost always means `sailor.datalyzerint.com` isn't on this account's egress allowlist yet — tell the user to check Admin settings → Capabilities rather than assuming the API itself is down.
- This is read-only. There is no write/update/delete access through this API — for changing lead data, direct the user to the Sailor web app itself.

## Example

```bash
curl -s -H "X-API-Key: hKLGWjAuPurTkdX-gO1bQnqERDLKsBptRYwthIT6t4A" \
  "https://sailor.datalyzerint.com/api/v1/leads/?stage=New&page_size=50"
```
