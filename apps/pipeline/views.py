"""
Pipeline views.

board()      — main kanban board, supports HTMX partial refresh for filters
move_lead()  — HTMX POST endpoint called when a card is dragged to a new column
"""

from django.shortcuts import render, get_object_or_404, redirect
from django.http import HttpResponse, HttpResponseBadRequest
from django.views.decorators.http import require_http_methods
import base64
import re
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.leads.models import Lead, Company, LeadSource
from apps.pipeline.models import PipelineStage, LeadStageHistory
from apps.actions.models import Action, ActionType
from apps.users.models import User
from apps.imports.models import ImportBatch


def _current_sailor_user(request):
    sailor = request.session.get("sailor_user") or {}
    email = sailor.get("email")
    if not email:
        return None
    try:
        return User.objects.get(email__iexact=email)
    except User.DoesNotExist:
        return None


def _is_admin(request):
    """Return True if the logged-in user has the admin role."""
    sailor = request.session.get("sailor_user") or {}
    role   = sailor.get("role", "")
    if role == "admin":
        return True
    # Fallback: check DB (handles sessions created before role was stored)
    user = _current_sailor_user(request)
    return user is not None and user.role == "admin"


def _can_access_lead(request, lead):
    """
    Admin can access any lead.
    Sales/viewer can only access leads assigned to them.
    """
    if _is_admin(request):
        return True
    user = _current_sailor_user(request)
    return user is not None and lead.assigned_to_id == user.id


def board(request):
    """
    Main pipeline board.

    Returns the full page on a normal GET.
    Returns only the #board-columns partial on an HTMX request (filter change).
    """

    # Non-terminal stages become board columns
    stages = list(PipelineStage.objects.filter(is_terminal=False).order_by("order"))

    # ── Filters ───────────────────────────────────────────────────────────────
    filters = {
        "assigned_to": request.GET.get("assigned_to", "").strip(),
        "source":      request.GET.get("source", "").strip(),
        "city":        request.GET.get("city", "").strip(),
        "search":      request.GET.get("search", "").strip(),
        "batch":       request.GET.get("batch", "").strip(),
    }

    leads_qs = (
        Lead.objects
        .select_related("company", "assigned_to", "source", "current_stage")
        .filter(deleted_at__isnull=True)
        .exclude(current_stage__isnull=True)
        .filter(current_stage__is_terminal=False)
        .order_by("-created_at")
    )

    # ── Role-based scoping ────────────────────────────────────────────────────
    if not _is_admin(request):
        current_user = _current_sailor_user(request)
        if current_user:
            leads_qs = leads_qs.filter(assigned_to=current_user)

    if filters["assigned_to"]:
        leads_qs = leads_qs.filter(assigned_to_id=filters["assigned_to"])
    if filters["source"]:
        leads_qs = leads_qs.filter(source_id=filters["source"])
    if filters["city"]:
        leads_qs = leads_qs.filter(city__icontains=filters["city"])
    if filters["batch"]:
        leads_qs = leads_qs.filter(import_batch_id=filters["batch"])
    if filters["search"]:
        q = filters["search"]
        leads_qs = leads_qs.filter(
            first_name__icontains=q
        ) | leads_qs.filter(
            last_name__icontains=q
        ) | leads_qs.filter(
            email__icontains=q
        ) | leads_qs.filter(
            company__name__icontains=q
        )

    # ── Group leads by stage ──────────────────────────────────────────────────
    # Build a dict: stage_id → [lead, ...]
    bucket = {stage.id: [] for stage in stages}
    for lead in leads_qs:
        if lead.current_stage_id in bucket:
            bucket[lead.current_stage_id].append(lead)

    # Pass as ordered list of (stage, leads) tuples — no custom template tag needed
    stages_with_leads = [(stage, bucket.get(stage.id, [])) for stage in stages]

    is_admin       = _is_admin(request)
    current_user   = _current_sailor_user(request)

    # Campaign enrollment counts per lead for list view badge
    from apps.campaigns.models import CampaignLead
    from django.db.models import Count
    all_lead_ids = [lead.id for _, leads in stages_with_leads for lead in leads]
    campaign_counts = {
        str(lead_id): c
        for lead_id, c in CampaignLead.objects
        .filter(lead_id__in=all_lead_ids, status__in=["active", "replied", "completed"])
        .values("lead_id")
        .annotate(c=Count("id"))
        .values_list("lead_id", "c")
    } if all_lead_ids else {}

    context = {
        "stages_with_leads": stages_with_leads,
        "users":          User.objects.filter(is_active=True).order_by("display_name"),
        "sources":        LeadSource.objects.filter(is_active=True).order_by("name"),
        "batches":        ImportBatch.objects.exclude(batch_name="").order_by("-created_at"),
        "filters":        filters,
        "total_leads":    sum(len(leads) for _, leads in stages_with_leads),
        "has_filters":    any(filters.values()),
        "is_admin":       is_admin,
        "current_user":   current_user,
        "campaign_counts": campaign_counts,
    }

    # HTMX partial request → return both kanban + list so both views stay filtered
    if request.headers.get("HX-Request"):
        return render(request, "pipeline/partials/pipeline_views.html", context)

    return render(request, "pipeline/board.html", context)


@require_http_methods(["POST"])
def bulk_move_leads(request):
    """
    POST /leads/bulk-move/
    Moves multiple leads to a new stage.
    Accepts: lead_ids[] (list of UUIDs), stage_id (UUID)
    Returns: JSON count for the JS toolbar to display feedback.
    """
    from django.http import JsonResponse
    lead_ids = request.POST.getlist("lead_ids")
    stage_id = request.POST.get("stage_id", "").strip()

    if not lead_ids or not stage_id:
        return JsonResponse({"error": "lead_ids and stage_id required"}, status=400)

    new_stage = get_object_or_404(PipelineStage, pk=stage_id)
    _sailor   = request.session.get("sailor_user", {})
    _actor    = None
    if _sailor.get("email"):
        try:
            _actor = User.objects.get(email__iexact=_sailor["email"])
        except User.DoesNotExist:
            pass

    moved = 0
    for lead in Lead.objects.filter(pk__in=lead_ids, deleted_at__isnull=True):
        if str(lead.current_stage_id) != str(stage_id):
            LeadStageHistory.objects.create(
                lead=lead,
                from_stage=lead.current_stage,
                to_stage=new_stage,
                changed_by=_actor,
                reason="Bulk moved via pipeline list view",
                auto_changed=False,
            )
            moved += 1

    return JsonResponse({"moved": moved, "stage": new_stage.name})


@require_http_methods(["POST"])
def bulk_delete_leads(request):
    """
    POST /leads/bulk-delete/
    Permanently (hard) deletes the selected leads and everything that
    references them — AI scores/insights, pipeline stage history, action
    logs, campaign enrollments/sends, lead-duplicate records — via the
    ON DELETE CASCADE constraints on those foreign keys.

    NOTE: this intentionally does a real .delete(), not the usual
    soft-delete (deleted_at) pattern used elsewhere in this app. The
    import dedup check in apps/imports/utils.py matches against
    Lead.all_objects, which includes soft-deleted rows — so a soft
    delete would NOT stop a re-import from flagging these as duplicates
    again. A hard delete is required for "delete this bad batch so I can
    re-import cleanly" to actually work.

    Accepts: lead_ids[] (list of UUIDs)
    Returns: JSON count of leads deleted for the JS toolbar to display feedback.
    """
    from django.http import JsonResponse
    lead_ids = request.POST.getlist("lead_ids")

    if not lead_ids:
        return JsonResponse({"error": "lead_ids required"}, status=400)

    qs = Lead.all_objects.filter(pk__in=lead_ids)
    count = qs.count()
    qs.delete()

    return JsonResponse({"deleted": count})


@require_http_methods(["POST"])
def move_lead(request, lead_id):
    """
    HTMX endpoint: called when a card is dropped into a new column.
    Writes a LeadStageHistory row (signal updates Lead.current_stage).
    Returns the updated card HTML.
    """
    lead = get_object_or_404(Lead, pk=lead_id, deleted_at__isnull=True)
    # Look up the acting user from session
    _sailor = request.session.get("sailor_user", {})
    _actor  = None
    if _sailor.get("email"):
        try:
            _actor = User.objects.get(email__iexact=_sailor["email"])
        except User.DoesNotExist:
            pass
    stage_id = request.POST.get("stage_id", "").strip()

    if not stage_id:
        return HttpResponseBadRequest("stage_id is required")

    new_stage = get_object_or_404(PipelineStage, pk=stage_id)

    # Only write history if the stage actually changed
    if str(lead.current_stage_id) != str(stage_id):
        LeadStageHistory.objects.create(
            lead=lead,
            from_stage=lead.current_stage,
            to_stage=new_stage,
            changed_by=_actor,
            reason="Moved via pipeline board",
            auto_changed=False,
        )
        lead.refresh_from_db()

    return render(request, "pipeline/partials/card.html", {"lead": lead})


# ── Manual lead add ───────────────────────────────────────────────────────────

def add_lead(request):
    """
    GET  → blank add-lead form.
    POST → create lead + initial stage history → redirect to board.
    """
    sources = LeadSource.objects.filter(is_active=True).order_by("name")
    users   = User.objects.filter(is_active=True).order_by("display_name")
    stages  = PipelineStage.objects.filter(is_terminal=False).order_by("order")
    _sailor = request.session.get("sailor_user", {})
    current_user = None
    if _sailor.get("email"):
        try:
            current_user = User.objects.get(email__iexact=_sailor["email"])
        except User.DoesNotExist:
            current_user = None

    if request.method == "POST":
        p = request.POST

        # ── Mandatory field validation ────────────────────────────────────────
        # IMPORTANT: validate BEFORE any DB writes (prevent orphan Company rows)
        errors = {}
        if not p.get("batch_name", "").strip():
            errors["batch_name"] = "Batch Name is required."
        if not p.get("source_id"):
            errors["source_id"] = "Source is required."
        if not p.get("assigned_to_id"):
            errors["assigned_to_id"] = "Assigned To is required."
        if errors:
            return render(request, "pipeline/add_lead.html", {
                "sources": sources,
                "users":   users,
                "stages":  stages,
                "errors":  errors,
                "prev":    p,
                "current_user_id": str(current_user.id) if current_user else "",
            })

        # ── Company (only reached after validation passes) ────────────────────
        company = None
        company_name = p.get("company_name", "").strip()
        city = p.get("city", "").strip()
        if company_name:
            company, _ = Company.objects.get_or_create(
                normalized_name=company_name.lower(),
                city=city,
                defaults={"name": company_name, "country": p.get("country", "India") or "India"},
            )

        # ── ImportBatch for manual add ────────────────────────────────────────
        batch = ImportBatch.objects.create(
            batch_name=p.get("batch_name", "").strip(),
            filename="manual",
            original_filename="manual",
            source_id=p.get("source_id") or None,
            uploaded_by=_current_sailor_user(request),
            status="done",
            total_rows=1,
            imported_rows=1,
        )

        # ── Lead ─────────────────────────────────────────────────────────────
        lead = Lead.objects.create(
            first_name   = p.get("first_name", "").strip() or "Unknown",
            last_name    = p.get("last_name", "").strip(),
            email        = p.get("email", "").strip().lower(),
            phone        = p.get("phone", "").strip(),
            title        = p.get("title", "").strip(),
            linkedin_url = p.get("linkedin_url", "").strip(),
            city         = city,
            state        = p.get("state", "").strip(),
            country      = p.get("country", "India").strip() or "India",
            company      = company,
            source_id    = p.get("source_id") or None,
            assigned_to_id = p.get("assigned_to_id") or None,
            import_batch = batch,
        )

        # ── Initial stage ─────────────────────────────────────────────────────
        stage_id = p.get("stage_id") or None
        if not stage_id:
            first_stage = stages.first()
            if first_stage:
                stage_id = str(first_stage.id)

        # Actor for audit trail
        _actor = current_user

        if stage_id:
            stage = get_object_or_404(PipelineStage, pk=stage_id)
            LeadStageHistory.objects.create(
                lead=lead,
                from_stage=None,
                to_stage=stage,
                changed_by=_actor,
                reason="Manually added via Sailor",
                auto_changed=False,
            )

        return redirect("/pipeline/")

    context = {
        "sources": sources,
        "users":   users,
        "stages":  stages,
        "current_user_id": str(current_user.id) if current_user else "",
    }
    return render(request, "pipeline/add_lead.html", context)


# ── Lead detail modal ─────────────────────────────────────────────────────────

def lead_detail(request, lead_id):
    """
    HTMX GET — returns the lead detail modal HTML.
    Loaded into #modal-content, which triggers openModal() via JS.
    """
    lead = get_object_or_404(
        Lead.objects.select_related("company", "assigned_to", "source", "current_stage"),
        pk=lead_id,
        deleted_at__isnull=True,
    )

    if not _can_access_lead(request, lead):
        return HttpResponse("You don't have permission to view this lead.", status=403)

    # Activity log — newest first for display
    activities = (
        Action.objects
        .select_related("action_type", "performed_by")
        .filter(lead=lead)
        .order_by("-performed_at")
    )

    # Stage history — newest first
    stage_history = (
        LeadStageHistory.objects
        .select_related("from_stage", "to_stage", "changed_by")
        .filter(lead=lead)
        .order_by("-changed_at")
    )

    action_types = ActionType.objects.all().order_by("category", "name")
    users = User.objects.filter(is_active=True).order_by("display_name")
    sibling_ids = list(
        Lead.objects
        .filter(current_stage=lead.current_stage)
        .order_by("-created_at", "id")
        .values_list("id", flat=True)
    )
    try:
        lead_position = sibling_ids.index(lead.id)
    except ValueError:
        lead_position = -1
    previous_lead_id = sibling_ids[lead_position - 1] if lead_position > 0 else None
    next_lead_id = (
        sibling_ids[lead_position + 1]
        if lead_position >= 0 and lead_position < len(sibling_ids) - 1
        else None
    )

    from apps.campaigns.models import EmailTemplate
    context = {
        "lead":            lead,
        "activities":      activities,
        "stage_history":   stage_history,
        "action_types":    action_types,
        "users":           users,
        "current_user":    _current_sailor_user(request),
        "previous_lead_id": previous_lead_id,
        "next_lead_id":    next_lead_id,
        "stages":          PipelineStage.objects.filter(is_terminal=False).order_by("order"),
        "email_templates": EmailTemplate.objects.all(),
    }
    return render(request, "pipeline/partials/lead_detail_modal.html", context)


@require_http_methods(["POST"])
def log_activity(request, lead_id):
    """
    HTMX POST — logs a new activity against a lead.
    Returns the updated activity feed partial to swap into the modal.
    """
    lead = get_object_or_404(Lead, pk=lead_id, deleted_at__isnull=True)
    p = request.POST

    action_type_id = p.get("action_type_id", "").strip()
    if not action_type_id:
        return HttpResponseBadRequest("action_type_id required")

    action_type = get_object_or_404(ActionType, pk=action_type_id)

    # Use now() if no performed_at provided
    performed_at_str = p.get("performed_at", "").strip()
    if performed_at_str:
        from django.utils.dateparse import parse_datetime
        performed_at = parse_datetime(performed_at_str) or timezone.now()
    else:
        performed_at = timezone.now()

    Action(
        lead=lead,
        action_type=action_type,
        performed_by=_current_sailor_user(request),
        performed_at=performed_at,
        outcome=p.get("outcome", "").strip(),
        metadata={"note": p.get("note", "").strip()} if p.get("note") else {},
    ).save()

    # Return refreshed activity list
    activities = (
        Action.objects
        .select_related("action_type", "performed_by")
        .filter(lead=lead)
        .order_by("-performed_at")
    )
    return render(request, "pipeline/partials/activity_feed.html", {
        "activities": activities,
        "lead": lead,
    })


# ── Edit lead ────────────────────────────────────────────────────────────────

def edit_lead(request, lead_id):
    """
    GET  → returns edit form HTML into #modal-content (HTMX swap).
    POST → saves changes, returns updated lead_detail_modal HTML.
    """
    lead = get_object_or_404(
        Lead.objects.select_related("company", "assigned_to", "source", "current_stage"),
        pk=lead_id,
        deleted_at__isnull=True,
    )

    sources = LeadSource.objects.filter(is_active=True).order_by("name")
    users   = User.objects.filter(is_active=True).order_by("display_name")

    if request.method == "POST":
        p = request.POST

        # ── Company ───────────────────────────────────────────────────────────
        company_name = p.get("company_name", "").strip()
        city         = p.get("city", "").strip()
        if company_name:
            company, _ = Company.objects.get_or_create(
                normalized_name=company_name.lower(),
                city=city,
                defaults={"name": company_name, "country": p.get("country", "India") or "India"},
            )
        else:
            company = None

        # ── Lead fields ───────────────────────────────────────────────────────
        lead.first_name    = p.get("first_name", "").strip() or lead.first_name
        lead.last_name     = p.get("last_name", "").strip()
        lead.email         = p.get("email", "").strip().lower()
        lead.phone         = p.get("phone", "").strip()
        lead.title         = p.get("title", "").strip()
        lead.department    = p.get("department", "").strip()
        lead.linkedin_url  = p.get("linkedin_url", "").strip()
        lead.city          = city
        lead.state         = p.get("state", "").strip()
        lead.country       = p.get("country", "India").strip() or "India"
        lead.company       = company
        lead.source_id     = p.get("source_id") or None
        lead.assigned_to_id = p.get("assigned_to_id") or None
        lead.save()

        # Return refreshed detail modal
        lead.refresh_from_db()
        activities    = Action.objects.select_related("action_type", "performed_by").filter(lead=lead).order_by("-performed_at")
        stage_history = LeadStageHistory.objects.select_related("from_stage", "to_stage", "changed_by").filter(lead=lead).order_by("-changed_at")
        action_types  = ActionType.objects.all().order_by("category", "name")
        return render(request, "pipeline/partials/lead_detail_modal.html", {
            "lead":          lead,
            "activities":    activities,
            "stage_history": stage_history,
            "action_types":  action_types,
            "users":         users,
            "stages":        PipelineStage.objects.filter(is_terminal=False).order_by("order"),
        })

    # GET — show edit form
    return render(request, "pipeline/partials/lead_edit_modal.html", {
        "lead":    lead,
        "sources": sources,
        "users":   users,
    })


# ── Delete lead (soft) ────────────────────────────────────────────────────────

@require_http_methods(["POST"])
def delete_lead(request, lead_id):
    """
    Soft-deletes a lead (sets deleted_at).
    Returns an HTMX response that closes the modal and removes the card from the board.
    """
    lead = get_object_or_404(Lead, pk=lead_id, deleted_at__isnull=True)
    Lead.all_objects.filter(pk=lead.pk).update(deleted_at=timezone.now())

    # HX-Trigger tells JS to close the modal and remove the card
    response = HttpResponse(status=200)
    response["HX-Trigger"] = f'{{"leadDeleted": "{lead_id}"}}'
    return response


# ── Mark as dead ──────────────────────────────────────────────────────────────

@require_http_methods(["POST"])
def mark_dead(request, lead_id):
    """
    Moves a lead to the first terminal (dead/inactive) stage.
    Returns updated modal content so the stage badge updates instantly.
    """
    lead = get_object_or_404(
        Lead.objects.select_related("company", "assigned_to", "source", "current_stage"),
        pk=lead_id,
        deleted_at__isnull=True,
    )

    dead_stage = PipelineStage.objects.filter(is_terminal=True).order_by("order").first()
    if not dead_stage:
        return HttpResponseBadRequest("No terminal stage configured.")

    # Look up the acting user from session
    _sailor = request.session.get("sailor_user", {})
    _actor  = None
    if _sailor.get("email"):
        try:
            _actor = User.objects.get(email__iexact=_sailor["email"])
        except User.DoesNotExist:
            pass
    if str(lead.current_stage_id) != str(dead_stage.id):
        LeadStageHistory.objects.create(
            lead=lead,
            from_stage=lead.current_stage,
            to_stage=dead_stage,
            changed_by=_actor,
            reason="Marked as dead/inactive via Sailor",
            auto_changed=False,
        )
        lead.refresh_from_db()

    # Return updated modal
    activities = Action.objects.select_related("action_type", "performed_by").filter(lead=lead).order_by("-performed_at")
    stage_history = LeadStageHistory.objects.select_related("from_stage", "to_stage", "changed_by").filter(lead=lead).order_by("-changed_at")
    action_types = ActionType.objects.all().order_by("category", "name")
    users = User.objects.filter(is_active=True).order_by("display_name")

    response = render(request, "pipeline/partials/lead_detail_modal.html", {
        "lead":          lead,
        "activities":    activities,
        "stage_history": stage_history,
        "action_types":  action_types,
        "users":         users,
        "stages":        PipelineStage.objects.filter(is_terminal=False).order_by("order"),
        "marked_dead":   True,
    })
    response["HX-Trigger"] = f'{{"leadMarkedDead": "{lead_id}"}}'
    return response


# ── Dead / Inactive leads board ───────────────────────────────────────────────

def dead_board(request):
    """
    Shows all leads currently in a terminal stage (dead / inactive / bounced).
    Supports the same filters as the active board.
    """
    filters = {
        "assigned_to": request.GET.get("assigned_to", "").strip(),
        "source":      request.GET.get("source", "").strip(),
        "search":      request.GET.get("search", "").strip(),
    }

    leads_qs = (
        Lead.objects
        .select_related("company", "assigned_to", "source", "current_stage")
        .filter(deleted_at__isnull=True, current_stage__is_terminal=True)
    )

    if filters["assigned_to"]:
        leads_qs = leads_qs.filter(assigned_to_id=filters["assigned_to"])
    if filters["source"]:
        leads_qs = leads_qs.filter(source_id=filters["source"])
    if filters["search"]:
        q = filters["search"]
        leads_qs = (
            leads_qs.filter(first_name__icontains=q)
            | leads_qs.filter(last_name__icontains=q)
            | leads_qs.filter(email__icontains=q)
            | leads_qs.filter(company__name__icontains=q)
        )

    # Group by terminal stage
    terminal_stages = PipelineStage.objects.filter(is_terminal=True).order_by("order")
    bucket = {s.id: [] for s in terminal_stages}
    for lead in leads_qs:
        if lead.current_stage_id in bucket:
            bucket[lead.current_stage_id].append(lead)

    stages_with_leads = [(stage, bucket.get(stage.id, [])) for stage in terminal_stages]

    context = {
        "stages_with_leads": stages_with_leads,
        "users":   User.objects.filter(is_active=True).order_by("display_name"),
        "sources": LeadSource.objects.filter(is_active=True).order_by("name"),
        "filters": filters,
        "total_leads": sum(len(leads) for _, leads in stages_with_leads),
        "has_filters": any(filters.values()),
    }

    if request.headers.get("HX-Request"):
        return render(request, "pipeline/partials/dead_board_columns.html", context)
    return render(request, "pipeline/dead_board.html", context)


# ── Dashboard ─────────────────────────────────────────────────────────────────

def dashboard(request):
    """
    Analytics dashboard: funnel, trends, source breakdown, per-user stats.
    Supports ?view=my (default) | ?view=team toggle — no role restriction.
    """
    from django.db.models import Count, Q
    from django.utils import timezone
    from datetime import timedelta, date
    import json

    now   = timezone.now()
    today = now.date()

    # ── Resolve logged-in user ────────────────────────────────────────────────
    sailor_user = request.session.get("sailor_user", {})
    my_email    = sailor_user.get("email", "")
    try:
        my_db_user = User.objects.get(email__iexact=my_email) if my_email else None
    except User.DoesNotExist:
        my_db_user = None

    # ── View mode: "my" (default) or "team" ──────────────────────────────────
    view_mode = request.GET.get("view", "team")
    if view_mode not in ("my", "team"):
        view_mode = "team"
    # Fall back to team view if user can't be resolved
    if view_mode == "my" and not my_db_user:
        view_mode = "team"

    # ── Base querysets ────────────────────────────────────────────────────────
    all_active  = Lead.objects.filter(deleted_at__isnull=True)
    if view_mode == "my":
        active_leads = all_active.filter(assigned_to=my_db_user)
    else:
        active_leads = all_active

    # ── Stat cards ────────────────────────────────────────────────────────────
    total_active = active_leads.filter(
        current_stage__is_terminal=False,
        current_stage__isnull=False
    ).count()

    converted = active_leads.filter(
        current_stage__name__iexact="Converted"
    ).count()

    dead = active_leads.filter(
        current_stage__name__iexact="Dead"
    ).count()

    # Leads added this week vs last week
    week_start      = today - timedelta(days=today.weekday())
    last_week_start = week_start - timedelta(days=7)
    leads_this_week = active_leads.filter(created_at__date__gte=week_start).count()
    leads_last_week = active_leads.filter(
        created_at__date__gte=last_week_start,
        created_at__date__lt=week_start
    ).count()
    week_delta = leads_this_week - leads_last_week

    # Conversion rate: converted / (converted + dead) × 100
    closed = converted + dead
    conversion_rate = round((converted / closed * 100), 1) if closed else 0

    # ── Funnel (non-terminal stages, ordered) ─────────────────────────────────
    stages = PipelineStage.objects.filter(is_terminal=False).order_by("order")
    funnel_labels = []
    funnel_counts = []
    for stage in stages:
        cnt = active_leads.filter(current_stage=stage).count()
        funnel_labels.append(stage.name)
        funnel_counts.append(cnt)

    # ── Leads by source (top 8) ───────────────────────────────────────────────
    source_data = (
        active_leads
        .filter(source__isnull=False)
        .values("source__name")
        .annotate(cnt=Count("id"))
        .order_by("-cnt")[:8]
    )
    source_labels = [d["source__name"] for d in source_data]
    source_counts = [d["cnt"] for d in source_data]

    # ── Leads created per day — last 30 days ──────────────────────────────────
    trend_days  = 30
    trend_start = today - timedelta(days=trend_days - 1)
    trend_qs = (
        active_leads
        .filter(created_at__date__gte=trend_start)
        .values("created_at__date")
        .annotate(cnt=Count("id"))
    )
    trend_map = {row["created_at__date"]: row["cnt"] for row in trend_qs}
    trend_labels = []
    trend_counts = []
    for i in range(trend_days):
        d = trend_start + timedelta(days=i)
        trend_labels.append(d.strftime("%d %b"))
        trend_counts.append(trend_map.get(d, 0))

    # ── Per-user stats (always all users — shown only in team view) ───────────
    user_stats = (
        User.objects
        .filter(is_active=True)
        .annotate(
            total=Count(
                "assigned_leads",
                filter=Q(
                    assigned_leads__deleted_at__isnull=True,
                    assigned_leads__current_stage__is_terminal=False
                )
            ),
            converted=Count(
                "assigned_leads",
                filter=Q(
                    assigned_leads__deleted_at__isnull=True,
                    assigned_leads__current_stage__name__iexact="Converted"
                )
            ),
        )
        .filter(total__gt=0)
        .order_by("-total")[:10]
    )

    # ── My funnel breakdown (my view sidebar detail) ───────────────────────────
    my_funnel = []
    if my_db_user and view_mode == "my":
        for stage in stages:
            my_funnel.append({
                "stage": stage.name,
                "count": active_leads.filter(current_stage=stage).count(),
            })

    # ── Recent activity — last 50 entries ────────────────────────────────────
    recent_activity = (
        LeadStageHistory.objects
        .select_related("lead", "to_stage", "from_stage", "changed_by")
        .order_by("-changed_at")[:50]
    )

    context = {
        # Mode
        "view_mode":        view_mode,
        # Stat cards (already scoped to view_mode)
        "total_active":     total_active,
        "leads_this_week":  leads_this_week,
        "week_delta":       week_delta,
        "conversion_rate":  conversion_rate,
        "total_converted":  converted,
        "dead":             dead,
        # Charts (JSON for Chart.js)
        "funnel_labels": json.dumps(funnel_labels),
        "funnel_counts": json.dumps(funnel_counts),
        "source_labels": json.dumps(source_labels),
        "source_counts": json.dumps(source_counts),
        "trend_labels":  json.dumps(trend_labels),
        "trend_counts":  json.dumps(trend_counts),
        # Tables
        "user_stats":       user_stats,
        "recent_activity":  recent_activity,
        "my_funnel":        my_funnel,
        "sailor_user":      sailor_user,
    }
    return render(request, "pipeline/dashboard.html", context)


@require_http_methods(["POST"])
def mark_bounced(request, lead_id):
    """
    Moves a lead to the Bounced/Invalid terminal stage.
    """
    lead = get_object_or_404(
        Lead.objects.select_related("company", "assigned_to", "source", "current_stage"),
        pk=lead_id,
        deleted_at__isnull=True,
    )

    bounced_stage = (
        PipelineStage.objects
        .filter(is_terminal=True, name__icontains="bounced")
        .first()
        or PipelineStage.objects.filter(is_terminal=True).order_by("order")[1:2].first()
    )
    if not bounced_stage:
        return HttpResponseBadRequest("No Bounced/Invalid stage configured.")

    # Look up the acting user from session
    _sailor = request.session.get("sailor_user", {})
    _actor  = None
    if _sailor.get("email"):
        try:
            _actor = User.objects.get(email__iexact=_sailor["email"])
        except User.DoesNotExist:
            pass
    if str(lead.current_stage_id) != str(bounced_stage.id):
        LeadStageHistory.objects.create(
            lead=lead,
            from_stage=lead.current_stage,
            to_stage=bounced_stage,
            changed_by=_actor,
            reason="Marked as bounced/invalid via Sailor",
            auto_changed=False,
        )
        lead.refresh_from_db()

    activities = Action.objects.select_related("action_type", "performed_by").filter(lead=lead).order_by("-performed_at")
    stage_history = LeadStageHistory.objects.select_related("from_stage", "to_stage", "changed_by").filter(lead=lead).order_by("-changed_at")
    action_types = ActionType.objects.all().order_by("category", "name")
    users = User.objects.filter(is_active=True).order_by("display_name")
    return render(request, "pipeline/partials/lead_detail_modal.html", {
        "lead": lead,
        "activities": activities,
        "stage_history": stage_history,
        "action_types": action_types,
        "users": users,
    })


@require_http_methods(["POST"])
def mark_converted(request, lead_id):
    """
    Moves a lead to the Converted terminal stage.
    """
    lead = get_object_or_404(
        Lead.objects.select_related("company", "assigned_to", "source", "current_stage"),
        pk=lead_id,
        deleted_at__isnull=True,
    )

    converted_stage = (
        PipelineStage.objects
        .filter(is_terminal=True, name__icontains="converted")
        .first()
    )
    if not converted_stage:
        return HttpResponseBadRequest("No Converted stage configured.")

    # Look up the acting user from session
    _sailor = request.session.get("sailor_user", {})
    _actor  = None
    if _sailor.get("email"):
        try:
            _actor = User.objects.get(email__iexact=_sailor["email"])
        except User.DoesNotExist:
            pass
    if str(lead.current_stage_id) != str(converted_stage.id):
        LeadStageHistory.objects.create(
            lead=lead,
            from_stage=lead.current_stage,
            to_stage=converted_stage,
            changed_by=_actor,
            reason="Marked as converted via Sailor",
            auto_changed=False,
        )
        lead.refresh_from_db()

    activities = Action.objects.select_related("action_type", "performed_by").filter(lead=lead).order_by("-performed_at")
    stage_history = LeadStageHistory.objects.select_related("from_stage", "to_stage", "changed_by").filter(lead=lead).order_by("-changed_at")
    action_types = ActionType.objects.all().order_by("category", "name")
    users = User.objects.filter(is_active=True).order_by("display_name")
    return render(request, "pipeline/partials/lead_detail_modal.html", {
        "lead": lead,
        "activities": activities,
        "stage_history": stage_history,
        "action_types": action_types,
        "users": users,
    })


# ── Send Email via Microsoft Graph ────────────────────────────────────────────

# 1×1 transparent GIF returned by the tracking pixel endpoint
_PIXEL_GIF = (
    b'\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x80\x00\x00'
    b'\xff\xff\xff\x00\x00\x00\x21\xf9\x04\x00\x00\x00\x00\x00'
    b'\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02\x44\x01\x00\x3b'
)


@require_http_methods(["POST"])
def send_email(request, lead_id):
    """
    HTMX POST: send an email to a lead via Microsoft Graph (Mail.Send).

    Accepts:
      - subject        (str, required)
      - body           (str, required)
      - attachments    (multi-file, optional, ≤5 MB each, ≤10 MB total)

    Returns an inline success/error partial targeted at #email-result.
    """
    from apps.core.auth_views import get_access_token
    from apps.core.graph_email import send_graph_email, GraphEmailError

    lead = get_object_or_404(
        Lead.objects.select_related("company", "assigned_to", "source", "current_stage"),
        pk=lead_id,
        deleted_at__isnull=True,
    )

    if not lead.email:
        return _email_response(False, "This lead has no email address on record.")

    subject = request.POST.get("subject", "").strip()
    body    = request.POST.get("body", "").strip()

    if not subject:
        return _email_response(False, "Subject is required.")
    if not body:
        return _email_response(False, "Message body is required.")

    access_token = get_access_token(request)
    if not access_token:
        return _email_response(
            False,
            "Session token expired. Please log out and log back in to send emails.",
        )

    # ── Process uploaded attachments ──────────────────────────────────────────
    attachments = []
    total_size  = 0
    for f in request.FILES.getlist("attachments"):
        if f.size > 5 * 1024 * 1024:
            return _email_response(False, f"Attachment \"{f.name}\" exceeds the 5 MB per-file limit.")
        total_size += f.size
        if total_size > 10 * 1024 * 1024:
            return _email_response(False, "Total attachment size exceeds 10 MB.")
        content = f.read()
        attachments.append({
            "@odata.type": "#microsoft.graph.fileAttachment",
            "name":         f.name,
            "contentType":  f.content_type or "application/octet-stream",
            "contentBytes": base64.b64encode(content).decode("utf-8"),
        })

    # ── Create Action first so we have an ID for the tracking pixel ──────────
    email_type = ActionType.objects.filter(category="email").first()
    _sailor    = request.session.get("sailor_user", {})
    _actor     = None
    if _sailor.get("email"):
        try:
            _actor = User.objects.get(email__iexact=_sailor["email"])
        except User.DoesNotExist:
            pass

    action = None
    if email_type:
        action = Action(
            lead=lead,
            action_type=email_type,
            performed_by=_actor,
            performed_at=timezone.now(),
            metadata={
                "note":           f"Email sent: \"{subject}\"",
                "email_subject":  subject,
                "email_body":     body,
                "email_to":       lead.email,
                "email_status":   "sent",
                "has_attachment": bool(attachments),
            },
        )
        action.save()

    # ── Build HTML body with optional tracking pixel ──────────────────────────
    body_html = "<p>" + body.replace("\n\n", "</p><p>").replace("\n", "<br>") + "</p>"
    if action:
        try:
            pixel_url = request.build_absolute_uri(f"/email-pixel/{action.id}/")
            body_html += (
                f'<img src="{pixel_url}" width="1" height="1" '
                f'style="display:none;border:0;outline:0" alt=""/>'
            )
        except Exception:
            pass

    # ── Build Reply-To address (for reply tracking) ───────────────────────────
    reply_to_email = None
    if action:
        from django.conf import settings as django_settings
        reply_domain = getattr(django_settings, "SAILOR_REPLY_DOMAIN", "")
        if reply_domain:
            reply_to_email = f"sailor+{action.id}@{reply_domain}"

    # ── Call Graph API ────────────────────────────────────────────────────────
    try:
        send_graph_email(
            access_token=access_token,
            to_email=lead.email,
            subject=subject,
            body_html=body_html,
            attachments=attachments,
            reply_to_email=reply_to_email,
        )
    except GraphEmailError as exc:
        # Mark action as failed (bypass append-only via queryset.update)
        if action:
            meta = dict(action.metadata)
            meta["email_status"] = "failed"
            meta["error"] = exc.message
            Action.objects.filter(pk=action.pk).update(metadata=meta)
        return _email_response(False, exc.message)

    return _email_response(True, f"Email sent to {lead.email}")


@require_http_methods(["POST"])
def email_reply_incoming(request, action_id):
    """
    POST /email-reply/<action_id>/

    Two uses:
      1. Manual — HTMX button in the activity feed ("Replied?" button).
         Returns a refreshed activity feed partial so the badge updates instantly.
      2. Automated webhook — called when a reply arrives at the Reply-To address
         (mail routing rule or Zapier). Returns 200 with no body.

    No session auth required — action_id acts as an unguessable token.
    """
    action = Action.objects.filter(
        pk=action_id, action_type__category="email"
    ).select_related("lead").first()

    if action:
        meta = dict(action.metadata)
        meta["reply_count"] = int(meta.get("reply_count") or 0) + 1
        meta["email_status"] = "replied"
        meta["last_replied_at"] = timezone.now().isoformat()
        Action.objects.filter(pk=action_id).update(metadata=meta)

        # ── Campaign exit-on-reply ────────────────────────────────────────────
        # If this action was a campaign send, mark the enrollment as replied
        # and exit if the campaign has exit_on_reply = True.
        try:
            from apps.campaigns.models import CampaignLead, CampaignSend
            cs = CampaignSend.objects.filter(action=action).select_related(
                "campaign_lead__campaign"
            ).first()
            if cs:
                now = timezone.now()
                CampaignSend.objects.filter(pk=cs.pk).update(
                    status=CampaignSend.STATUS_REPLIED,
                    replied_at=now,
                    reply_count=cs.reply_count + 1,
                )
                cl = cs.campaign_lead
                if cl.status == CampaignLead.STATUS_ACTIVE:
                    CampaignLead.objects.filter(pk=cl.pk).update(
                        status=CampaignLead.STATUS_REPLIED,
                        exit_reason="Replied to email",
                        exited_at=now,
                    )
                    if cl.campaign.exit_on_reply:
                        # Cancel remaining queued sends for this lead
                        CampaignSend.objects.filter(
                            campaign_lead=cl,
                            status=CampaignSend.STATUS_QUEUED,
                        ).update(
                            status=CampaignSend.STATUS_SKIPPED,
                            error_message="Lead replied — auto-exited from campaign",
                        )
        except Exception:
            pass  # Never let campaign logic break the reply endpoint

    # If called from HTMX (has HX-Request header), return refreshed feed
    if request.headers.get("HX-Request") and action:
        activities = (
            Action.objects
            .select_related("action_type", "performed_by")
            .filter(lead=action.lead)
            .order_by("-performed_at")
        )
        return render(request, "pipeline/partials/activity_feed.html", {
            "activities": activities,
            "lead": action.lead,
        })

    return HttpResponse(status=200)


def email_pixel(request, action_id):
    """
    Public endpoint — serve a 1×1 tracking pixel and record the open event.

    Called automatically when the recipient's email client loads images.
    Uses queryset.update() to bypass the Action append-only constraint,
    because tracking is a system event, not a user edit.
    """
    try:
        action = Action.objects.filter(
            pk=action_id, action_type__category="email"
        ).first()
        if action and action.metadata.get("email_status") in {"sent", "opened"}:
            meta = dict(action.metadata)
            meta["open_count"] = int(meta.get("open_count") or 0) + 1
            meta["email_status"] = "opened"
            meta["opened_at"]    = timezone.now().isoformat()
            Action.objects.filter(pk=action_id).update(metadata=meta)
    except Exception:
        pass  # Never let tracking errors surface

    response = HttpResponse(_PIXEL_GIF, content_type="image/gif")
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response["Pragma"]        = "no-cache"
    response["Expires"]       = "0"
    return response


def _email_response(success: bool, message: str) -> HttpResponse:
    """Inline HTMX success/error partial targeted at #email-result."""
    if success:
        html = f"""
<div id="email-result"
     class="flex items-center gap-2 mt-3 p-3 bg-emerald-50 border border-emerald-200
            rounded-xl text-xs text-emerald-700 font-medium">
  <svg class="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/>
  </svg>
  {message}
</div>"""
    else:
        html = f"""
<div id="email-result"
     class="flex items-center gap-2 mt-3 p-3 bg-red-50 border border-red-200
            rounded-xl text-xs text-red-700 font-medium">
  <svg class="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
          d="M12 9v2m0 4h.01M12 3a9 9 0 110 18A9 9 0 0112 3z"/>
  </svg>
  {message}
</div>"""
    return HttpResponse(html, status=200 if success else 400)
