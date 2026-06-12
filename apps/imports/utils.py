"""
Import utilities.

parse_file()         — reads xlsx/csv, returns (headers, preview_rows, total_rows)
auto_detect()        — guesses column→field mappings from header names
process_import()     — runs the full import: dedup, company get/create, lead insert

Deduplication strategy (industry-standard, 4-layer):
  1. Email          — case-insensitive exact match (primary unique key)
  2. LinkedIn URL   — normalize to username slug, exact match
  3. Phone          — strip non-digits, match last 10 digits
  4. Name + Company — normalize both, exact match (catches typos in email/phone)

All four checks run against the entire Lead table (including soft-deleted rows).
A "duplicate reason" is recorded in the batch error_log for every skipped row.
"""

import csv
import io
import os
import re
import uuid
from typing import Any

from django.utils import timezone

# ── Field catalogue (DB-backed) ───────────────────────────────────────────────

def get_field_choices() -> list[tuple[str, str]]:
    from apps.imports.models import LeadFieldConfig
    choices = [("__skip__", "— Skip this column —")]
    choices += [
        (f.field_key, f.display_label)
        for f in LeadFieldConfig.objects.filter(is_active=True).order_by("sort_order", "field_key")
    ]
    return choices


def get_auto_map_keywords() -> dict[str, list[str]]:
    from apps.imports.models import LeadFieldConfig
    result = {}
    for f in LeadFieldConfig.objects.filter(is_active=True).exclude(detect_keywords=[]):
        if f.detect_keywords:
            result[f.field_key] = f.detect_keywords
    return result


class _LazyFieldChoices(list):
    """Transparent list proxy that populates itself from the DB on first use."""
    _loaded = False

    def _ensure_loaded(self):
        if not self._loaded:
            self.clear()
            self.extend(get_field_choices())
            self._loaded = True

    def __iter__(self):
        self._ensure_loaded()
        return super().__iter__()

    def __len__(self):
        self._ensure_loaded()
        return super().__len__()


LEAD_FIELD_CHOICES = _LazyFieldChoices()


def auto_detect(headers: list[str]) -> dict[str, str]:
    """
    Returns {original_header: field_name} for headers we can confidently detect.
    Undetected headers map to '__skip__'.
    """
    keywords_map = get_auto_map_keywords()
    result = {}
    for header in headers:
        h = header.lower().strip()
        best_field = "__skip__"
        best_length = 0
        for field, keywords in keywords_map.items():
            for kw in keywords:
                if kw in h and len(kw) > best_length:
                    best_field = field
                    best_length = len(kw)
        result[header] = best_field
    return result


# ── File parsing ──────────────────────────────────────────────────────────────

def parse_file(file_path: str, preview_rows: int = 5) -> tuple[list[str], list[dict], int]:
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".csv":
        return _parse_csv(file_path, preview_rows)
    elif ext in (".xlsx", ".xls"):
        return _parse_xlsx(file_path, preview_rows)
    else:
        raise ValueError(f"Unsupported file type: {ext}")


def _parse_csv(file_path: str, preview_n: int) -> tuple[list[str], list[dict], int]:
    rows = []
    with open(file_path, encoding="utf-8-sig", errors="replace") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        for row in reader:
            rows.append(dict(row))
    headers = list(headers)
    return headers, rows[:preview_n], len(rows)


def _parse_xlsx(file_path: str, preview_n: int) -> tuple[list[str], list[dict], int]:
    import openpyxl
    wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
    ws = wb.active
    all_rows = list(ws.iter_rows(values_only=True))
    wb.close()

    if not all_rows:
        return [], [], 0

    headers = [str(h).strip() if h is not None else f"Column {i+1}"
               for i, h in enumerate(all_rows[0])]
    data_rows = []
    for row in all_rows[1:]:
        data_rows.append({
            headers[i]: (str(v).strip() if v is not None else "")
            for i, v in enumerate(row)
        })
    return headers, data_rows[:preview_n], len(data_rows)


# ── Dedup helpers ─────────────────────────────────────────────────────────────

def _normalize_email(email: str) -> str:
    return email.lower().strip()


def _normalize_phone(phone: str) -> str:
    """Strip non-digits; return last 10 digits (strips country codes).
    Returns '' if fewer than 10 digits."""
    digits = re.sub(r"\D", "", phone)
    return digits[-10:] if len(digits) >= 10 else ""


def _extract_linkedin_slug(url_or_id: str) -> str:
    """
    Normalise a LinkedIn profile URL or raw username to a lowercase slug.
    linkedin.com/in/john-doe-123/ → 'john-doe-123'
    """
    if not url_or_id:
        return ""
    m = re.search(r"linkedin\.com/in/([^/?#\s]+)", url_or_id, re.IGNORECASE)
    if m:
        return m.group(1).rstrip("/").lower()
    # Bare ID (no URL) — return normalised
    return url_or_id.strip().lower()


def _normalize_name(name: str) -> str:
    """Keep only lowercase alphanumeric chars."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _normalize_company(name: str) -> str:
    """Remove legal suffixes and punctuation, lowercase."""
    name = name.lower()
    # Strip common legal suffixes
    name = re.sub(
        r"\b(pvt|private|ltd|limited|llc|inc|corp|corporation|co|the|&|and)\b",
        "", name,
    )
    return re.sub(r"[^a-z0-9]", "", name)


def _build_dedup_sets(all_rows: list[dict], column_mapping: dict[str, str]) -> dict[str, set]:
    """
    Single-pass over the file rows to collect candidate keys,
    then a small number of bulk DB queries to build lookup sets.
    Much faster than one DB query per row for large files.
    """
    from apps.leads.models import Lead

    # Collect candidate values from the file
    file_emails:    set[str] = set()
    file_phones:    set[str] = set()
    file_li_slugs:  set[str] = set()
    file_name_cos:  set[str] = set()

    for row in all_rows:
        f = _map_row(row, column_mapping)
        email = _normalize_email(f.get("email", ""))
        if email:
            file_emails.add(email)

        phone = _normalize_phone(f.get("phone", ""))
        if phone:
            file_phones.add(phone)

        li_url = f.get("linkedin_url", "") or f.get("linkedin_id", "")
        li_slug = _extract_linkedin_slug(li_url)
        if li_slug:
            file_li_slugs.add(li_slug)

        first = _normalize_name(f.get("first_name", ""))
        last  = _normalize_name(f.get("last_name", ""))
        co    = _normalize_company(f.get("company__name", ""))
        if first and co:
            file_name_cos.add(f"{first}{last}|{co}")

    # ── Bulk DB lookups ───────────────────────────────────────────────────────
    existing_emails: set[str] = set()
    existing_phones: set[str] = set()
    existing_li:     set[str] = set()
    existing_name_co: set[str] = set()

    if file_emails:
        existing_emails = set(
            Lead.all_objects
            .filter(email__in=file_emails)
            .values_list("email", flat=True)
        )

    if file_phones:
        # Normalize all DB phones on the fly — we only pull leads that have
        # a phone matching one of the file's normalized phones.
        # We can't index-normalize in SQL easily, so we pull all non-empty phones
        # and normalize in Python.  For very large DBs add a stored normalized_phone
        # column instead.
        db_phones = Lead.all_objects.exclude(phone="").values_list("phone", flat=True)
        for p in db_phones:
            np = _normalize_phone(p)
            if np in file_phones:
                existing_phones.add(np)

    if file_li_slugs:
        # linkedin_url and linkedin_id columns
        from django.db.models import Q
        db_li = Lead.all_objects.filter(
            Q(linkedin_url__isnull=False) | Q(linkedin_id__isnull=False)
        ).values_list("linkedin_url", "linkedin_id")
        for li_url, li_id in db_li:
            slug = _extract_linkedin_slug(li_url or "") or _extract_linkedin_slug(li_id or "")
            if slug in file_li_slugs:
                existing_li.add(slug)

    if file_name_cos:
        # Pull first_name, last_name, company.name for name+company matching
        db_names = Lead.all_objects.select_related("company").values_list(
            "first_name", "last_name", "company__name"
        )
        for fn, ln, cn in db_names:
            if not fn or not cn:
                continue
            key = f"{_normalize_name(fn)}{_normalize_name(ln or '')}|{_normalize_company(cn)}"
            if key in file_name_cos:
                existing_name_co.add(key)

    return {
        "emails":   existing_emails,
        "phones":   existing_phones,
        "li_slugs": existing_li,
        "name_cos": existing_name_co,
    }


def _is_duplicate(fields: dict, sets: dict) -> tuple[bool, str]:
    """
    Returns (True, reason) if this row matches any existing lead.
    Checks in priority order: email → LinkedIn → phone → name+company.
    """
    email = _normalize_email(fields.get("email", ""))
    if email and email in sets["emails"]:
        return True, f"duplicate email ({email})"

    li_url = fields.get("linkedin_url", "") or fields.get("linkedin_id", "")
    li_slug = _extract_linkedin_slug(li_url)
    if li_slug and li_slug in sets["li_slugs"]:
        return True, f"duplicate LinkedIn ({li_slug})"

    phone = _normalize_phone(fields.get("phone", ""))
    if phone and phone in sets["phones"]:
        return True, f"duplicate phone ({fields.get('phone', '')})"

    first = _normalize_name(fields.get("first_name", ""))
    last  = _normalize_name(fields.get("last_name", ""))
    co    = _normalize_company(fields.get("company__name", ""))
    if first and co:
        key = f"{first}{last}|{co}"
        if key in sets["name_cos"]:
            fn = fields.get("first_name", "")
            ln = fields.get("last_name", "")
            return True, f"duplicate name+company ({fn} {ln} at {fields.get('company__name', '')})"

    return False, ""


# ── Import processing ─────────────────────────────────────────────────────────

def process_import(
    batch_id: str,
    file_path: str,
    column_mapping: dict[str, str],
    source_id: str | None = None,
    assigned_to_id: str | None = None,
) -> dict[str, Any]:
    """
    Reads the full file, maps columns, deduplicates, and inserts leads.
    Returns a summary dict used to render the results step.

    Dedup layers (checked in order, first match wins):
      1. Email — case-insensitive exact
      2. LinkedIn URL/ID — slug-normalized exact
      3. Phone — last-10-digits exact
      4. Full name + Company — normalized exact
    """
    from apps.imports.models import ImportBatch
    from apps.leads.models import Lead, Company, LeadSource
    from apps.pipeline.models import PipelineStage, LeadStageHistory
    from apps.users.models import User

    batch = ImportBatch.objects.get(id=batch_id)
    batch.status = "processing"
    batch.save(update_fields=["status", "updated_at"])

    default_stage = PipelineStage.objects.filter(is_terminal=False).order_by("order").first()
    source        = LeadSource.objects.filter(id=source_id).first() if source_id else None
    assigned_to   = User.objects.filter(id=assigned_to_id).first() if assigned_to_id else None

    headers, all_rows, total = _full_parse(file_path)

    # ── Pre-build dedup sets (bulk DB queries, O(1) per-row lookups) ──────────
    dedup_sets = _build_dedup_sets(all_rows, column_mapping)

    # Also track emails/phones/slugs seen within THIS batch to catch
    # duplicates inside the same file
    seen_emails:   set[str] = set()
    seen_phones:   set[str] = set()
    seen_li_slugs: set[str] = set()
    seen_name_cos: set[str] = set()

    imported   = 0
    duplicates = 0
    skipped    = 0
    errors: list[dict] = []

    for row_num, row in enumerate(all_rows, start=2):
        try:
            fields = _map_row(row, column_mapping)

            if not any(fields.values()):
                skipped += 1
                continue

            # ── Dedup: check against DB ───────────────────────────────────────
            is_dup, reason = _is_duplicate(fields, dedup_sets)

            # ── Dedup: check within-file duplicates ───────────────────────────
            if not is_dup:
                email = _normalize_email(fields.get("email", ""))
                if email:
                    if email in seen_emails:
                        is_dup, reason = True, f"duplicate email in file ({email})"
                    else:
                        seen_emails.add(email)

                if not is_dup:
                    phone = _normalize_phone(fields.get("phone", ""))
                    if phone:
                        if phone in seen_phones:
                            is_dup, reason = True, f"duplicate phone in file ({fields.get('phone', '')})"
                        else:
                            seen_phones.add(phone)

                if not is_dup:
                    li_url  = fields.get("linkedin_url", "") or fields.get("linkedin_id", "")
                    li_slug = _extract_linkedin_slug(li_url)
                    if li_slug:
                        if li_slug in seen_li_slugs:
                            is_dup, reason = True, f"duplicate LinkedIn in file ({li_slug})"
                        else:
                            seen_li_slugs.add(li_slug)

                if not is_dup:
                    fn  = _normalize_name(fields.get("first_name", ""))
                    ln  = _normalize_name(fields.get("last_name", ""))
                    co  = _normalize_company(fields.get("company__name", ""))
                    if fn and co:
                        key = f"{fn}{ln}|{co}"
                        if key in seen_name_cos:
                            is_dup, reason = True, (
                                f"duplicate name+company in file "
                                f"({fields.get('first_name','')} {fields.get('last_name','')} "
                                f"at {fields.get('company__name','')})"
                            )
                        else:
                            seen_name_cos.add(key)

            if is_dup:
                errors.append({
                    "row":    row_num,
                    "error":  f"Skipped — {reason}",
                    "data":   {k: v for k, v in row.items() if v},
                    "is_dup": True,
                })
                duplicates += 1
                continue

            # ── Company get / create ──────────────────────────────────────────
            company = None
            company_name = fields.pop("company__name", "").strip()

            _COMPANY_KEY_ALIASES = {"num_employees": "employee_count"}
            company_fields: dict[str, str] = {}
            for key in list(fields.keys()):
                if key.startswith("company__"):
                    raw_key    = key[len("company__"):]
                    mapped_key = _COMPANY_KEY_ALIASES.get(raw_key, raw_key)
                    company_fields[mapped_key] = fields.pop(key)

            company_city = company_fields.get("city", "") or fields.get("city", "").strip()

            if company_name:
                normalized = company_name.lower().strip()
                defaults: dict = {"name": company_name, "country": company_fields.get("country", "India")}
                defaults.update(company_fields)
                company, created = Company.objects.get_or_create(
                    normalized_name=normalized,
                    city=company_city,
                    defaults=defaults,
                )
                if not created:
                    updated_fields = []
                    for field_name, value in company_fields.items():
                        if value and not getattr(company, field_name, None):
                            setattr(company, field_name, value)
                            updated_fields.append(field_name)
                    if updated_fields:
                        company.save(update_fields=updated_fields + ["updated_at"])

            # ── Build lead kwargs ─────────────────────────────────────────────
            email      = _normalize_email(fields.get("email", ""))
            linkedin_id = fields.get("linkedin_id", "").strip()
            lead_kwargs = {
                "first_name":      fields.get("first_name") or "Unknown",
                "last_name":       fields.get("last_name", ""),
                "email":           email,
                "phone":           fields.get("phone", ""),
                "title":           fields.get("title", ""),
                "department":      fields.get("department", ""),
                "sub_department":  fields.get("sub_department", ""),
                "corporate_phone": fields.get("corporate_phone", ""),
                "website":         fields.get("website", ""),
                "linkedin_url":    fields.get("linkedin_url", ""),
                "linkedin_id":     linkedin_id,
                "facebook_url":    fields.get("facebook_url", ""),
                "twitter_url":     fields.get("twitter_url", ""),
                "city":            fields.get("city", ""),
                "state":           fields.get("state", ""),
                "country":         fields.get("country", "India") or "India",
                "company":         company,
                "source":          source,
                "assigned_to":     assigned_to,
                "import_batch":    batch,
                "raw_import_data": row,
            }

            lead = Lead.objects.create(**lead_kwargs)

            if default_stage:
                LeadStageHistory.objects.create(
                    lead=lead,
                    from_stage=None,
                    to_stage=default_stage,
                    changed_by=assigned_to,
                    reason=f"Imported from {batch.original_filename}",
                    auto_changed=True,
                )

            imported += 1

        except Exception as exc:
            errors.append({"row": row_num, "error": str(exc), "data": row, "is_dup": False})

    # ── Update batch ──────────────────────────────────────────────────────────
    batch.total_rows     = len(all_rows)
    batch.imported_rows  = imported
    batch.duplicate_rows = duplicates
    batch.skipped_rows   = skipped
    batch.error_log      = errors
    batch.status         = "done"
    batch.save(update_fields=[
        "total_rows", "imported_rows", "duplicate_rows",
        "skipped_rows", "error_log", "status", "updated_at",
    ])

    return {
        "batch":      batch,
        "imported":   imported,
        "duplicates": duplicates,
        "skipped":    skipped,
        "errors":     errors,
        "total":      len(all_rows),
    }


def _full_parse(file_path: str) -> tuple[list[str], list[dict], int]:
    """Parse entire file (no preview limit)."""
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".csv":
        return _parse_csv(file_path, preview_n=9_999_999)
    return _parse_xlsx(file_path, preview_n=9_999_999)


def _map_row(row: dict, column_mapping: dict[str, str]) -> dict[str, str]:
    """
    Applies column_mapping to a raw row dict.
    Handles full_name → first_name + last_name splitting.
    """
    result: dict[str, str] = {}
    for file_header, lead_field in column_mapping.items():
        if lead_field == "__skip__":
            continue
        value = str(row.get(file_header, "")).strip()
        if not value:
            continue
        if lead_field == "full_name":
            parts = value.split(" ", 1)
            result["first_name"] = parts[0].strip()
            result["last_name"]  = parts[1].strip() if len(parts) > 1 else ""
        else:
            result[lead_field] = value
    return result
