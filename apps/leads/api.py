"""
Minimal read-only JSON API for external integrations (e.g. Claude Cowork).

Protected by a static API key (LEADS_API_KEY setting), sent as the
X-API-Key header on every request. Intentionally separate from the
Azure AD session auth used by the rest of the app — this is meant for
server-to-server / integration use, not browser users.

Mounted under /api/v1/ (see config/urls.py -> apps.leads.urls).
"""

from __future__ import annotations

from django.conf import settings
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import JsonResponse
from django.views.decorators.http import require_GET

from apps.leads.models import Lead

MAX_PAGE_SIZE = 200
DEFAULT_PAGE_SIZE = 50


def _api_key_valid(request) -> bool:
    configured = getattr(settings, "LEADS_API_KEY", "")
    if not configured:
        # Fail closed: if no key is configured on the server, the endpoint
        # is disabled rather than open.
        return False
    provided = request.headers.get("X-API-Key", "")
    return provided == configured


def _unauthorized() -> JsonResponse:
    return JsonResponse({"error": "Invalid or missing API key."}, status=401)


def _serialize_lead(lead: Lead) -> dict:
    return {
        "id": str(lead.id),
        "first_name": lead.first_name,
        "last_name": lead.last_name,
        "full_name": lead.full_name,
        "email": lead.email,
        "email_verified": lead.email_verified,
        "email_bounced": lead.email_bounced,
        "phone": lead.phone,
        "title": lead.title,
        "department": lead.department,
        "sub_department": lead.sub_department,
        "linkedin_url": lead.linkedin_url,
        "city": lead.city,
        "state": lead.state,
        "country": lead.country,
        "company": (
            {
                "id": str(lead.company_id),
                "name": lead.company.name,
                "industry": lead.company.industry,
                "city": lead.company.city,
                "website": lead.company.website,
            }
            if lead.company_id
            else None
        ),
        "source": lead.source.name if lead.source_id else None,
        "import_batch": lead.import_batch.batch_name if lead.import_batch_id else None,
        "current_stage": lead.current_stage.name if lead.current_stage_id else None,
        "assigned_to": (
            {
                "id": str(lead.assigned_to_id),
                "name": lead.assigned_to.display_name,
                "email": lead.assigned_to.email,
            }
            if lead.assigned_to_id
            else None
        ),
        "ai_score": lead.ai_score,
        "converted_at": lead.converted_at.isoformat() if lead.converted_at else None,
        "dead_at": lead.dead_at.isoformat() if lead.dead_at else None,
        "created_at": lead.created_at.isoformat(),
        "updated_at": lead.updated_at.isoformat(),
    }


@require_GET
def leads_list(request):
    """
    GET /api/v1/leads/

    Query params (all optional):
      city           - case-insensitive substring match on Lead.city
      batch          - import_batch UUID (exact match)
      stage          - pipeline stage name (case-insensitive exact match)
      source         - lead source name (case-insensitive exact match)
      q              - free-text search across name / email / company name
      updated_since  - ISO-8601 datetime; only leads updated at/after this
      page           - page number, default 1
      page_size      - results per page, default 50, max 200
    """
    if not _api_key_valid(request):
        return _unauthorized()

    qs = (
        Lead.objects.select_related(
            "company", "source", "import_batch", "current_stage", "assigned_to"
        )
        .order_by("-created_at")
    )

    city = request.GET.get("city")
    if city:
        qs = qs.filter(city__icontains=city)

    batch = request.GET.get("batch")
    if batch:
        qs = qs.filter(import_batch_id=batch)

    stage = request.GET.get("stage")
    if stage:
        qs = qs.filter(current_stage__name__iexact=stage)

    source = request.GET.get("source")
    if source:
        qs = qs.filter(source__name__iexact=source)

    q = request.GET.get("q")
    if q:
        qs = qs.filter(
            Q(first_name__icontains=q)
            | Q(last_name__icontains=q)
            | Q(email__icontains=q)
            | Q(company__name__icontains=q)
        )

    updated_since = request.GET.get("updated_since")
    if updated_since:
        qs = qs.filter(updated_at__gte=updated_since)

    try:
        page_size = min(int(request.GET.get("page_size", DEFAULT_PAGE_SIZE)), MAX_PAGE_SIZE)
        if page_size < 1:
            page_size = DEFAULT_PAGE_SIZE
    except ValueError:
        page_size = DEFAULT_PAGE_SIZE

    try:
        page_number = int(request.GET.get("page", 1))
    except ValueError:
        page_number = 1

    paginator = Paginator(qs, page_size)
    page = paginator.get_page(page_number)

    return JsonResponse(
        {
            "count": paginator.count,
            "page": page.number,
            "num_pages": paginator.num_pages,
            "page_size": page_size,
            "results": [_serialize_lead(lead) for lead in page.object_list],
        }
    )


@require_GET
def lead_detail(request, lead_id):
    """GET /api/v1/leads/<uuid>/"""
    if not _api_key_valid(request):
        return _unauthorized()

    try:
        lead = Lead.objects.select_related(
            "company", "source", "import_batch", "current_stage", "assigned_to"
        ).get(id=lead_id)
    except Lead.DoesNotExist:
        return JsonResponse({"error": "Not found."}, status=404)

    return JsonResponse(_serialize_lead(lead))
