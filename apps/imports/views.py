"""
Import wizard views — 3-step HTMX flow.

Step 1  /imports/           GET  → upload form
        /imports/upload/    POST → parse file, return mapping UI (HTMX swap)
Step 2  /imports/mapping/   POST → run import, return results (HTMX swap)
Step 3  Results embedded in the wizard page

Session keys used:
  import_file_path     : absolute path to the saved upload
  import_headers       : list of header strings
  import_preview_rows  : first 10 rows as list of dicts
  import_total_rows    : int
"""

import os
import uuid

from django.conf import settings
from django.shortcuts import render, get_object_or_404
from django.http import HttpResponse, HttpResponseBadRequest
from django.views.decorators.http import require_http_methods
from django.utils import timezone

from apps.imports.models import ImportBatch, ColumnMappingTemplate
from apps.imports.utils import (
    get_field_choices,
    parse_file,
    auto_detect,
    process_import,
)
from apps.leads.models import LeadSource
from apps.users.models import User

ALLOWED_EXTENSIONS = {".csv", ".xlsx", ".xls"}
MAX_FILE_SIZE_MB = 10


# ── Step 1 — Wizard shell ─────────────────────────────────────────────────────

def wizard(request):
    """Main import page — renders the wizard shell with upload step."""
    context = {
        "sources": LeadSource.objects.filter(is_active=True).order_by("name"),
        "users":   User.objects.filter(is_active=True).order_by("display_name"),
        "templates": ColumnMappingTemplate.objects.select_related("source").order_by("name"),
    }
    return render(request, "imports/wizard.html", context)


# ── Step 1 → 2 — Upload & parse ───────────────────────────────────────────────

@require_http_methods(["POST"])
def upload(request):
    """
    Receives the uploaded file.
    Parses headers + preview rows.
    Returns the mapping step HTML via HTMX.
    """
    uploaded = request.FILES.get("file")
    if not uploaded:
        return _error_partial("No file received. Please select a file.")

    # Validate extension
    _, ext = os.path.splitext(uploaded.name)
    if ext.lower() not in ALLOWED_EXTENSIONS:
        return _error_partial(f"Unsupported file type '{ext}'. Please upload .csv or .xlsx")

    # Validate size
    if uploaded.size > MAX_FILE_SIZE_MB * 1024 * 1024:
        return _error_partial(f"File too large. Maximum size is {MAX_FILE_SIZE_MB} MB.")

    # Save file to media/imports/
    save_dir = os.path.join(settings.MEDIA_ROOT, "imports")
    os.makedirs(save_dir, exist_ok=True)
    filename = f"{uuid.uuid4().hex}{ext.lower()}"
    file_path = os.path.join(save_dir, filename)

    with open(file_path, "wb") as f:
        for chunk in uploaded.chunks():
            f.write(chunk)

    # Parse
    try:
        headers, preview_rows, total_rows = parse_file(file_path, preview_rows=10)
    except Exception as exc:
        os.remove(file_path)
        return _error_partial(f"Could not read file: {exc}")

    if not headers:
        os.remove(file_path)
        return _error_partial("The file appears to be empty or has no header row.")

    # Store in session for the mapping step
    request.session["import_file_path"]    = file_path
    request.session["import_headers"]      = headers
    request.session["import_preview_rows"] = preview_rows
    request.session["import_total_rows"]   = total_rows
    request.session["import_filename"]     = uploaded.name

    auto_mapping = auto_detect(headers)

    context = {
        "headers":       headers,
        "preview_rows":  preview_rows,
        "total_rows":    total_rows,
        "filename":      uploaded.name,
        "auto_mapping":  auto_mapping,
        "field_choices": get_field_choices(),
        "sources":       LeadSource.objects.filter(is_active=True).order_by("name"),
        "users":         User.objects.filter(is_active=True).order_by("display_name"),
        "templates":     ColumnMappingTemplate.objects.select_related("source").order_by("name"),
        "auto_count":    sum(1 for v in auto_mapping.values() if v != "__skip__"),
    }
    return render(request, "imports/steps/step2_mapping.html", context)


# ── Step 2 → 3 — Run import ───────────────────────────────────────────────────

@require_http_methods(["POST"])
def run_import(request):
    """
    Receives the column mapping form.
    Runs the import synchronously.
    Returns the results step HTML via HTMX.
    """
    file_path = request.session.get("import_file_path")
    filename  = request.session.get("import_filename", "upload")

    if not file_path or not os.path.exists(file_path):
        return _error_partial("Session expired or file missing. Please start the import again.")

    # ── Build column mapping from POST ────────────────────────────────────────
    # We use index-based keys (map__0, map__1, …) so we can look up the exact
    # original header name from the session instead of trying to reverse a slug.
    session_headers = request.session.get("import_headers", [])
    column_mapping = {}
    for i, header in enumerate(session_headers):
        value = request.POST.get(f"map__{i}")
        if value:
            column_mapping[header] = value

    if not column_mapping:
        return _error_partial("No column mapping received.")

    # ── Check at least one useful field is mapped ─────────────────────────────
    useful = [v for v in column_mapping.values() if v != "__skip__"]
    if not useful:
        return _error_partial("All columns are set to Skip. Please map at least one field.")

    source_id      = request.POST.get("source_id") or None
    assigned_to_id = request.POST.get("assigned_to_id") or None

    batch_name     = request.POST.get("batch_name", "").strip()

    # ── Mandatory field validation ─────────────────────────────────────────────
    if not batch_name:
        return _error_partial("Batch Name is required. Give this import a label so you can identify it later.")
    if not source_id:
        return _error_partial("Source is required. Please select a source before importing.")
    if not assigned_to_id:
        return _error_partial("Assigned To is required. Please select a team member before importing.")

    # ── Optionally save mapping as template ───────────────────────────────────
    save_as_template = request.POST.get("save_as_template") == "on"
    template_name    = request.POST.get("template_name", "").strip()
    if save_as_template and template_name:
        ColumnMappingTemplate.objects.get_or_create(
            name=template_name,
            defaults={
                "source_id":  source_id,
                "mapping":    column_mapping,
            },
        )

    # ── Create ImportBatch record ─────────────────────────────────────────────
    batch = ImportBatch.objects.create(
        batch_name=batch_name,
        filename=os.path.basename(file_path),
        original_filename=filename,
        source_id=source_id,
        uploaded_by_id=assigned_to_id,
        column_mapping=column_mapping,
        status="pending",
    )

    # ── Process ───────────────────────────────────────────────────────────────
    try:
        summary = process_import(
            batch_id=str(batch.id),
            file_path=file_path,
            column_mapping=column_mapping,
            source_id=source_id,
            assigned_to_id=assigned_to_id,
        )
    except Exception as exc:
        batch.status = "failed"
        batch.error_log = [{"row": "—", "error": str(exc), "data": {}}]
        batch.save(update_fields=["status", "error_log", "updated_at"])
        return _error_partial(f"Import failed: {exc}")

    # Clear session
    for key in ["import_file_path", "import_headers", "import_preview_rows",
                "import_total_rows", "import_filename"]:
        request.session.pop(key, None)

    # Delete temp file — it contains raw lead data and should not live on disk
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
    except OSError:
        pass  # non-critical — don't fail a successful import over cleanup

    return render(request, "imports/steps/step3_results.html", summary)


# ── Template API ──────────────────────────────────────────────────────────────

def load_template(request, template_id):
    """
    HTMX GET: returns the saved mapping for a template as JSON,
    consumed by JS to pre-select the dropdowns.
    """
    import json
    tmpl = get_object_or_404(ColumnMappingTemplate, id=template_id)
    return HttpResponse(json.dumps(tmpl.mapping), content_type="application/json")



# ── Helper ────────────────────────────────────────────────────────────────────

def _error_partial(message: str) -> HttpResponse:
    html = f"""
    <div class="flex items-center gap-3 bg-red-50 border border-red-200 rounded-xl p-4 text-sm text-red-700">
      <svg class="w-5 h-5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
              d="M12 9v2m0 4h.01M12 3a9 9 0 110 18A9 9 0 0112 3z"/>
      </svg>
      <span>{message}</span>
    </div>
    """
    return HttpResponse(html, status=400)
