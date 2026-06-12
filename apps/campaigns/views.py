"""
Campaigns views — rebuilt for Sailor v2.
"""

import random
import time
import re
from datetime import timedelta
from django.db.models import Exists, OuterRef

from django.shortcuts import render, get_object_or_404, redirect
from django.views.decorators.http import require_http_methods, require_POST
from django.utils import timezone
from django.db.models import Count, Max, Q
from django.http import HttpResponse

from apps.campaigns.models import Campaign, CampaignStep, CampaignLead, CampaignSend, EmailTemplate
from apps.leads.models import Lead
from apps.pipeline.models import PipelineStage, LeadStageHistory
from apps.users.models import User
from apps.actions.models import Action, ActionType
from apps.core.graph_email import send_graph_email, GraphEmailError


def _campaign_activities(campaign, limit=50):
    """Return recent actions for all leads enrolled in this campaign."""
    enrolled_lead_ids = campaign.leads.values_list("lead_id", flat=True)
    return (
        Action.objects
        .filter(lead_id__in=enrolled_lead_ids)
        .select_related("action_type", "performed_by", "lead")
        .order_by("-performed_at")[:limit]
    )

BATCH_SIZE = 20
_TOKEN_RE  = re.compile(r"\{\{(\w+)\}\}")

_PIXEL_GIF = (
    b'\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x80\x00\x00'
    b'\xff\xff\xff\x00\x00\x00\x21\xf9\x04\x00\x00\x00\x00'
    b'\x00\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02'
    b'\x44\x01\x00\x3b'
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_token(request):
    try:
        from apps.core.auth_views import get_access_token
        return get_access_token(request)
    except Exception:
        return None


def _sailor_user(request):
    return request.session.get("sailor_user", {})


def _db_user(request):
    sailor = _sailor_user(request)
    try:
        return User.objects.get(email__iexact=sailor.get("email", ""))
    except User.DoesNotExist:
        return None


def _render_template(template: str, lead, sender_name: str, pixel_url: str = "") -> str:
    values = {
        "first_name":  lead.first_name or "",
        "last_name":   lead.last_name  or "",
        "full_name":   f"{lead.first_name} {lead.last_name}".strip(),
        "company":     (lead.company.name if lead.company_id else "") or "",
        "job_title":   getattr(lead, "title", "") or "",
        "sender_name": sender_name,
    }
    result = _TOKEN_RE.sub(lambda m: values.get(m.group(1), m.group(0)), template)
    # If the body is plain text (no HTML tags), convert newlines to <br> so
    # line breaks entered in the textarea are preserved in the rendered email.
    if "<" not in result:
        result = result.replace("\n", "<br>\n")
    if pixel_url:
        result += f'\n<img src="{pixel_url}" width="1" height="1" style="display:none" alt="">'
    return result


def _steps_grouped(campaign):
    step_groups = {}
    for s in campaign.steps.all():
        step_groups.setdefault(s.step_number, []).append(s)
    return [{"step_number": k, "variants": v} for k, v in sorted(step_groups.items())]


# ── List ──────────────────────────────────────────────────────────────────────

def campaign_list(request):
    campaigns = (
        Campaign.objects
        .select_related("created_by")
        .annotate(
            enrolled_count=Count("leads", distinct=True),
            sent_count=Count(
                "leads__sends",
                filter=Q(leads__sends__status__in=["sent", "opened", "replied"]),
                distinct=True,
            ),
            open_count=Count(
                "leads__sends",
                filter=Q(leads__sends__status__in=["opened", "replied"]),
                distinct=True,
            ),
            replied_count=Count(
                "leads",
                filter=Q(leads__status="replied"),
                distinct=True,
            ),
        )
    )
    return render(request, "campaigns/campaign_list.html", {
        "campaigns":   campaigns,
        "sailor_user": _sailor_user(request),
    })


# ── Create ────────────────────────────────────────────────────────────────────

@require_http_methods(["GET", "POST"])
def campaign_create(request):
    pre_lead_ids = request.GET.getlist("lead_ids") or request.POST.getlist("pre_lead_ids")
    pre_leads    = list(Lead.objects.filter(pk__in=pre_lead_ids, deleted_at__isnull=True).select_related("company")) if pre_lead_ids else []

    if request.method == "POST":
        name          = request.POST.get("name", "").strip()
        goal          = request.POST.get("goal", "").strip()
        exit_on_reply = request.POST.get("exit_on_reply") == "on"
        errors        = {}
        if not name:
            errors["name"] = "Campaign name is required."
        if not errors:
            c = Campaign.objects.create(
                name=name, goal=goal,
                exit_on_reply=exit_on_reply,
                created_by=_db_user(request),
            )
            if pre_lead_ids:
                db_user = _db_user(request)
                for lead in pre_leads:
                    CampaignLead.objects.get_or_create(
                        campaign=c, lead=lead,
                        defaults={"enrolled_by": db_user, "status": CampaignLead.STATUS_ACTIVE},
                    )
            return redirect("campaigns:campaign_detail", campaign_id=c.pk)
        return render(request, "campaigns/campaign_create.html", {
            "errors": errors, "post": request.POST,
            "pre_leads": pre_leads, "pre_lead_ids": pre_lead_ids,
        })

    from apps.campaigns.models import generate_campaign_name
    return render(request, "campaigns/campaign_create.html", {
        "suggested_name": generate_campaign_name(),
        "pre_leads":      pre_leads,
        "pre_lead_ids":   pre_lead_ids,
    })


# ── Detail ────────────────────────────────────────────────────────────────────

def campaign_detail(request, campaign_id):
    campaign    = get_object_or_404(Campaign, pk=campaign_id)
    step_groups = _steps_grouped(campaign)

    enrolled = (
        campaign.leads
        .select_related("lead", "lead__company", "enrolled_by")
        .annotate(
            has_pending_manual=Exists(
                CampaignSend.objects.filter(
                    campaign_lead=OuterRef("pk"),
                    status=CampaignSend.STATUS_QUEUED,
                    step__step_type__in=[CampaignStep.TYPE_LINKEDIN, CampaignStep.TYPE_TASK],
                )
            )
        )
        .order_by("-enrolled_at")
    )

    step_analytics = (
        CampaignSend.objects
        .filter(campaign_lead__campaign=campaign)
        .values("step__step_number", "step__label", "step__step_type", "variant_label")
        .annotate(
            total   = Count("id"),
            sent    = Count("id", filter=Q(status__in=["sent", "opened", "replied", "bounced"])),
            opens   = Count("id", filter=Q(status__in=["opened", "replied"])),
        )
        .order_by("step__step_number", "variant_label")
    )

    all_sends     = CampaignSend.objects.filter(campaign_lead__campaign=campaign)
    total_sent    = all_sends.filter(status__in=["sent", "opened", "replied"]).count()
    total_opens   = all_sends.filter(status__in=["opened", "replied"]).count()
    total_replied = campaign.leads.filter(status=CampaignLead.STATUS_REPLIED).count()
    total_enrolled = campaign.leads.count()

    # Materialise queryset and add open_pct per row
    step_analytics_list = list(step_analytics)
    for row in step_analytics_list:
        row["open_pct"] = round(row["opens"] / row["sent"] * 100) if row["sent"] else 0
    step_analytics = step_analytics_list

    now = timezone.now()
    active_queued_qs = CampaignSend.objects.filter(
        campaign_lead__campaign=campaign,
        campaign_lead__status=CampaignLead.STATUS_ACTIVE,
        status=CampaignSend.STATUS_QUEUED,
    )
    # Emails: only "due" when scheduled time has passed (auto-sendable)
    # Manual tasks: show ALL queued — time doesn't matter, they always need human action
    pending_due_email    = active_queued_qs.filter(scheduled_for__lte=now, step__step_type=CampaignStep.TYPE_EMAIL).count()
    pending_due_manual   = active_queued_qs.filter(step__step_type__in=[CampaignStep.TYPE_LINKEDIN, CampaignStep.TYPE_TASK]).count()
    future_scheduled     = active_queued_qs.filter(scheduled_for__gt=now, step__step_type=CampaignStep.TYPE_EMAIL)
    next_send            = future_scheduled.order_by("scheduled_for").values_list("scheduled_for", flat=True).first()
    future_scheduled_count = future_scheduled.count()

    # All non-deleted leads, each annotated with whether they're already enrolled
    all_leads = (
        Lead.objects
        .filter(deleted_at__isnull=True)
        .annotate(
            is_enrolled=Exists(
                CampaignLead.objects.filter(
                    campaign=campaign,
                    lead=OuterRef("pk"),
                )
            )
        )
        .select_related("company")
        .order_by("last_name", "first_name")
    )

    return render(request, "campaigns/campaign_detail.html", {
        "campaign":          campaign,
        "step_groups":       step_groups,
        "enrolled":          enrolled,
        "step_analytics":    step_analytics,
        "total_sent":        total_sent,
        "total_opens":       total_opens,
        "total_replied":     total_replied,
        "total_enrolled":    total_enrolled,
        "open_rate":         round(total_opens / total_sent * 100, 1) if total_sent else 0,
        "reply_rate":        round(total_replied / total_enrolled * 100, 1) if total_enrolled else 0,
        "pending_due":           pending_due_email,
        "pending_due_manual":    pending_due_manual,
        "future_scheduled":      future_scheduled_count,
        "next_send":             next_send,
        "all_leads":         all_leads,
        "email_templates":   EmailTemplate.objects.all(),
        "action_types":      ActionType.objects.all().order_by("category", "name"),
        "sailor_user":       _sailor_user(request),
        "step_type_choices": CampaignStep.TYPE_CHOICES,
        "stages":            PipelineStage.objects.filter(is_terminal=False).order_by("order"),
        "recent_activities": _campaign_activities(campaign),
    })


# ── Status ────────────────────────────────────────────────────────────────────

@require_POST
def campaign_set_status(request, campaign_id):
    campaign   = get_object_or_404(Campaign, pk=campaign_id)
    new_status = request.POST.get("status", "")
    if new_status in {c[0] for c in Campaign.STATUS_CHOICES}:
        campaign.status = new_status
        campaign.save(update_fields=["status", "updated_at"])
    return redirect("campaigns:campaign_detail", campaign_id=campaign_id)


# ── Steps ─────────────────────────────────────────────────────────────────────

@require_http_methods(["GET", "POST"])
def campaign_add_step(request, campaign_id):
    campaign = get_object_or_404(Campaign, pk=campaign_id)
    if request.method == "POST":
        step_type        = request.POST.get("step_type", CampaignStep.TYPE_EMAIL)
        label            = request.POST.get("label", "").strip()
        scheduled_at_str = request.POST.get("scheduled_at", "").strip()
        subject_template = request.POST.get("subject_template", "").strip()
        body_html        = request.POST.get("body_html_template", "").strip()
        task_description = request.POST.get("task_description", "").strip()

        # Parse scheduled_at (HTML datetime-local format: "2026-06-05T14:30")
        scheduled_at = None
        if scheduled_at_str:
            from django.utils.dateparse import parse_datetime
            scheduled_at = parse_datetime(scheduled_at_str + ":00") or parse_datetime(scheduled_at_str)
            if scheduled_at and timezone.is_naive(scheduled_at):
                scheduled_at = timezone.make_aware(scheduled_at)

        existing_max = campaign.steps.aggregate(m=Max("step_number"))["m"] or 0
        step_number  = existing_max + 1

        errors = {}
        if step_type == CampaignStep.TYPE_EMAIL and not subject_template:
            errors["subject_template"] = "Subject required."
        if step_type == CampaignStep.TYPE_EMAIL and not body_html:
            errors["body_html_template"] = "Body required."
        if step_type in (CampaignStep.TYPE_LINKEDIN, CampaignStep.TYPE_TASK) and not task_description:
            errors["task_description"] = "Description required."

        # Process attachments (stored as base64 JSON on the step)
        import json as _json
        import base64 as _base64
        attachments_data = []
        for f in request.FILES.getlist("attachments"):
            if f.size <= 5 * 1024 * 1024:
                attachments_data.append({
                    "name":         f.name,
                    "content_type": f.content_type or "application/octet-stream",
                    "data_b64":     _base64.b64encode(f.read()).decode("utf-8"),
                })

        send_now_flag = request.POST.get("send_now") == "1"

        if not errors:
            step = CampaignStep.objects.create(
                campaign=campaign,
                step_number=step_number,
                variant_label="A",
                step_type=step_type,
                label=label,
                scheduled_at=scheduled_at,
                subject_template=subject_template,
                body_html_template=body_html,
                task_description=task_description,
                attachments_json=_json.dumps(attachments_data),
            )

            # Create CampaignSend rows for ALL enrolled leads (active OR completed)
            now = timezone.now()
            all_enrollments = campaign.leads.filter(
                status__in=[CampaignLead.STATUS_ACTIVE, CampaignLead.STATUS_COMPLETED]
            )
            for cl in all_enrollments:
                if not CampaignSend.objects.filter(campaign_lead=cl, step=step).exists():
                    CampaignSend.objects.create(
                        campaign_lead=cl,
                        step=step,
                        variant_label="A",
                        scheduled_for=scheduled_at if scheduled_at else now,
                    )
                # Always reset back to active — they have a new pending send
                CampaignLead.objects.filter(pk=cl.pk).update(status=CampaignLead.STATUS_ACTIVE)

            # If "Save & Send Now" clicked — immediately send
            if send_now_flag and step_type == CampaignStep.TYPE_EMAIL:
                token = _get_token(request)
                if token:
                    sender_name = _sailor_user(request).get("display_name", "The Team")
                    email_type  = ActionType.objects.filter(category="email").first()
                    db_user     = _db_user(request)
                    sent = failed = 0
                    due_sends = CampaignSend.objects.filter(
                        campaign_lead__campaign=campaign,
                        step=step,
                        status=CampaignSend.STATUS_QUEUED,
                        scheduled_for__lte=now,
                    ).select_related("campaign_lead__lead__company")
                    for cs in due_sends:
                        lead = cs.campaign_lead.lead
                        if not lead.email:
                            CampaignSend.objects.filter(pk=cs.pk).update(
                                status=CampaignSend.STATUS_SKIPPED, error_message="No email"
                            )
                            continue
                        pixel_url = request.build_absolute_uri(f"/campaigns/pixel/{cs.pk}/")
                        subj = _render_template(step.subject_template, lead, sender_name)
                        body_rendered = _render_template(step.body_html_template, lead, sender_name, pixel_url)
                        action = None
                        if email_type:
                            action = Action.objects.create(
                                lead=lead, action_type=email_type,
                                performed_by=db_user, performed_at=now,
                                metadata={
                                    "note": f"[Campaign: {campaign.name}] {subj}",
                                    "email_subject": subj, "email_body": body_rendered,
                                    "email_to": lead.email, "email_status": "sent",
                                    "campaign_id": str(campaign.pk), "campaign_name": campaign.name,
                                    "campaign_step": step.step_number,
                                    "has_attachment": bool(attachments_data),
                                },
                            )
                        step_attachments = [
                            {"@odata.type": "#microsoft.graph.fileAttachment",
                             "name": a["name"], "contentType": a["content_type"],
                             "contentBytes": a["data_b64"]}
                            for a in attachments_data
                        ]
                        try:
                            send_graph_email(access_token=token, to_email=lead.email,
                                             subject=subj, body_html=body_rendered,
                                             attachments=step_attachments or None)
                            CampaignSend.objects.filter(pk=cs.pk).update(
                                status=CampaignSend.STATUS_SENT, sent_at=now,
                                action_id=action.pk if action else None,
                            )
                            CampaignLead.objects.filter(pk=cs.campaign_lead_id).update(
                                current_step=step.step_number
                            )
                            sent += 1
                        except GraphEmailError as exc:
                            CampaignSend.objects.filter(pk=cs.pk).update(
                                status=CampaignSend.STATUS_FAILED, error_message=str(exc)
                            )
                            failed += 1
                        time.sleep(2)

            return redirect("campaigns:campaign_detail", campaign_id=campaign_id)

        if request.headers.get("HX-Request"):
            return render(request, "campaigns/partials/step_form.html", {
                "campaign": campaign, "errors": errors, "post": request.POST,
            })
        return redirect("campaigns:campaign_detail", campaign_id=campaign_id)

    max_step = campaign.steps.aggregate(m=Max("step_number"))["m"] or 0
    return render(request, "campaigns/partials/step_form.html", {
        "campaign": campaign, "next_step": max_step + 1,
        "step_type_choices": CampaignStep.TYPE_CHOICES,
    })


@require_POST
def campaign_delete_step(request, campaign_id, step_id):
    campaign = get_object_or_404(Campaign, pk=campaign_id)
    step     = get_object_or_404(CampaignStep, pk=step_id, campaign=campaign)
    step.delete()
    if request.headers.get("HX-Request"):
        return _render_steps_partial(request, campaign)
    return redirect("campaigns:campaign_detail", campaign_id=campaign_id)


def _render_steps_partial(request, campaign):
    return render(request, "campaigns/partials/steps_list.html", {
        "campaign":    campaign,
        "step_groups": _steps_grouped(campaign),
    })


# ── Enroll ────────────────────────────────────────────────────────────────────

@require_POST
def campaign_enroll(request, campaign_id):
    campaign = get_object_or_404(Campaign, pk=campaign_id)
    lead_ids = request.POST.getlist("lead_ids")

    if not lead_ids:
        none_html = '<p class="text-slate-500 text-xs font-medium p-2 bg-slate-50 border border-slate-100 rounded-lg">No leads selected. Check at least one lead and try again.</p>'
        return HttpResponse(none_html)

    now      = timezone.now()
    db_user  = _db_user(request)
    leads    = Lead.objects.filter(pk__in=lead_ids, deleted_at__isnull=True)
    steps    = list(campaign.steps.order_by("step_number", "variant_label"))

    if not steps:
        msg = "Add at least one step before enrolling leads."
        err_html = f'<p class="text-red-600 text-xs font-medium p-2 bg-red-50 border border-red-100 rounded-lg">⚠ {msg}</p>'
        if request.headers.get("HX-Request"):
            return HttpResponse(err_html)
        # Non-HTMX fallback: redirect — error won't be visible, so always treat as HTMX-compatible
        return HttpResponse(err_html)

    enrolled_count = 0
    for lead in leads:
        cl, created = CampaignLead.objects.get_or_create(
            campaign=campaign, lead=lead,
            defaults={"enrolled_by": db_user, "status": CampaignLead.STATUS_ACTIVE},
        )
        if not created:
            continue
        enrolled_count += 1
        # Do NOT queue any existing steps for newly-enrolled leads.
        # New leads should only receive steps added AFTER their enrollment.
        # campaign_add_step already creates sends for all active leads when
        # a new step is saved, so new leads will naturally pick up future steps.

    if enrolled_count == 0:
        already_html = '<p class="text-amber-700 text-xs font-medium p-2 bg-amber-50 border border-amber-100 rounded-lg">All selected leads are already enrolled.</p>'
        if request.headers.get("HX-Request"):
            return HttpResponse(already_html)
        return HttpResponse(already_html)

    # Reload the page so the enrolled list and "In campaign" badges refresh
    response = HttpResponse()
    response["HX-Refresh"] = "true"
    return response


# ── Send now ──────────────────────────────────────────────────────────────────

@require_POST
def campaign_send_now(request, campaign_id):
    campaign = get_object_or_404(Campaign, pk=campaign_id)
    token    = _get_token(request)
    if not token:
        msg = "Session token expired — please re-login."
        if request.headers.get("HX-Request"):
            return HttpResponse(f'<p class="text-amber-700 text-xs p-2 bg-amber-50 rounded-lg">{msg}</p>')
        return redirect("campaigns:campaign_detail", campaign_id=campaign_id)

    sailor      = _sailor_user(request)
    sender_name = sailor.get("display_name", "The Team")
    now         = timezone.now()
    email_type  = ActionType.objects.filter(category="email").first()
    db_user     = _db_user(request)

    due_sends = (
        CampaignSend.objects
        .filter(
            campaign_lead__campaign=campaign,
            campaign_lead__status__in=[CampaignLead.STATUS_ACTIVE, CampaignLead.STATUS_COMPLETED],
            status=CampaignSend.STATUS_QUEUED,
            scheduled_for__lte=now,
        )
        .select_related("campaign_lead__lead__company", "step")
        .order_by("scheduled_for")[:BATCH_SIZE]
    )

    sent_count = failed_count = skipped_count = 0

    for cs in due_sends:
        lead = cs.campaign_lead.lead
        if not lead.email:
            CampaignSend.objects.filter(pk=cs.pk).update(
                status=CampaignSend.STATUS_SKIPPED, error_message="No email address"
            )
            skipped_count += 1
            continue

        pixel_url = request.build_absolute_uri(f"/campaigns/pixel/{cs.pk}/")
        subject   = _render_template(cs.step.subject_template, lead, sender_name)
        body      = _render_template(cs.step.body_html_template, lead, sender_name, pixel_url)

        action = None
        if email_type:
            action = Action.objects.create(
                lead=lead,
                action_type=email_type,
                performed_by=db_user,
                performed_at=now,
                metadata={
                    "note":           f"[Campaign: {campaign.name}] {subject}",
                    "email_subject":  subject,
                    "email_body":     body,
                    "email_to":       lead.email,
                    "email_status":   "sent",
                    "campaign_id":    str(campaign.pk),
                    "campaign_name":  campaign.name,
                    "campaign_step":  cs.step.step_number,
                    "has_attachment": bool(cs.step.attachments_json and cs.step.attachments_json != "[]"),
                },
            )

        import json as _json
        step_atts = _json.loads(cs.step.attachments_json or "[]")
        graph_atts = [
            {"@odata.type": "#microsoft.graph.fileAttachment",
             "name": a["name"], "contentType": a["content_type"], "contentBytes": a["data_b64"]}
            for a in step_atts
        ]
        try:
            send_graph_email(
                access_token=token,
                to_email=lead.email,
                subject=subject,
                body_html=body,
                attachments=graph_atts or None,
            )
            CampaignSend.objects.filter(pk=cs.pk).update(
                status=CampaignSend.STATUS_SENT,
                sent_at=now,
                action_id=action.pk if action else None,
            )
            CampaignLead.objects.filter(pk=cs.campaign_lead_id).update(
                current_step=cs.step.step_number
            )
            # Queue the next step for this lead if it hasn't been queued yet
            next_step_number = cs.step.step_number + 1
            next_steps = list(campaign.steps.filter(step_number=next_step_number))
            if next_steps:
                next_step = random.choice(next_steps)
                cl = cs.campaign_lead
                if not CampaignSend.objects.filter(
                    campaign_lead=cl, step__step_number=next_step_number
                ).exists():
                    next_scheduled = next_step.scheduled_at if next_step.scheduled_at else now
                    CampaignSend.objects.create(
                        campaign_lead=cl,
                        step=next_step,
                        variant_label=next_step.variant_label,
                        scheduled_for=next_scheduled,
                    )
            sent_count += 1
        except GraphEmailError as exc:
            CampaignSend.objects.filter(pk=cs.pk).update(
                status=CampaignSend.STATUS_FAILED, error_message=str(exc)
            )
            if action:
                meta = dict(action.metadata)
                meta["email_status"] = "failed"
                meta["error"] = str(exc)
                Action.objects.filter(pk=action.pk).update(metadata=meta)
            failed_count += 1

        time.sleep(2)

    for cl in campaign.leads.filter(status=CampaignLead.STATUS_ACTIVE):
        if not cl.sends.filter(status=CampaignSend.STATUS_QUEUED).exists():
            CampaignLead.objects.filter(pk=cl.pk).update(status=CampaignLead.STATUS_COMPLETED)

    remaining = CampaignSend.objects.filter(
        campaign_lead__campaign=campaign,
        campaign_lead__status=CampaignLead.STATUS_ACTIVE,
        status=CampaignSend.STATUS_QUEUED,
        scheduled_for__lte=now,
    ).count()

    parts = []
    if sent_count:    parts.append(f"✓ {sent_count} sent")
    if failed_count:  parts.append(f"✗ {failed_count} failed")
    if skipped_count: parts.append(f"— {skipped_count} skipped")
    if not parts:     parts = ["No emails due right now"]
    if remaining:     parts.append(f"· {remaining} still due")
    colour = "emerald" if sent_count and not failed_count else ("red" if failed_count else "slate")
    msg = " &nbsp;·&nbsp; ".join(parts)

    if request.headers.get("HX-Request"):
        return HttpResponse(
            f'<p id="send-result" class="text-{colour}-700 text-xs font-medium p-3 bg-{colour}-50 rounded-lg">{msg}</p>'
        )
    return redirect("campaigns:campaign_detail", campaign_id=campaign_id)


# ── Mark manual step done (for all enrolled leads) ───────────────────────────

@require_POST
def campaign_mark_step_done(request, campaign_id, step_id):
    """Mark a LinkedIn/Task step as done for every enrolled lead that still has
    it queued, then queue the next step for each of those leads."""
    campaign = get_object_or_404(Campaign, pk=campaign_id)
    step     = get_object_or_404(CampaignStep, pk=step_id, campaign=campaign)

    now = timezone.now()

    pending_sends = (
        CampaignSend.objects
        .filter(
            step=step,
            status=CampaignSend.STATUS_QUEUED,
        )
        .select_related("campaign_lead")
    )

    next_steps = list(campaign.steps.filter(step_number=step.step_number + 1))

    for cs in pending_sends:
        CampaignSend.objects.filter(pk=cs.pk).update(
            status=CampaignSend.STATUS_SENT,
            sent_at=now,
        )
        CampaignLead.objects.filter(pk=cs.campaign_lead_id).update(
            current_step=step.step_number
        )
        # Queue the next step for this lead if not already queued
        if next_steps:
            next_step = random.choice(next_steps)
            if not CampaignSend.objects.filter(
                campaign_lead=cs.campaign_lead,
                step__step_number=step.step_number + 1,
            ).exists():
                CampaignSend.objects.create(
                    campaign_lead=cs.campaign_lead,
                    step=next_step,
                    variant_label=next_step.variant_label,
                    scheduled_for=next_step.scheduled_at if next_step.scheduled_at else now,
                )

    # Full page refresh so the pending-tasks warning banner recomputes
    if request.headers.get("HX-Request"):
        response = HttpResponse()
        response["HX-Refresh"] = "true"
        return response
    return redirect("campaigns:campaign_detail", campaign_id=campaign_id)


# ── Exit lead ─────────────────────────────────────────────────────────────────

@require_POST
def campaign_exit_lead(request, campaign_id, enrollment_id):
    campaign   = get_object_or_404(Campaign, pk=campaign_id)
    enrollment = get_object_or_404(CampaignLead, pk=enrollment_id, campaign=campaign)
    reason     = request.POST.get("reason", "Manually exited").strip() or "Manually exited"
    CampaignLead.objects.filter(pk=enrollment.pk).update(
        status=CampaignLead.STATUS_EXITED,
        exit_reason=reason,
        exited_at=timezone.now(),
    )
    CampaignSend.objects.filter(
        campaign_lead=enrollment, status=CampaignSend.STATUS_QUEUED,
    ).update(status=CampaignSend.STATUS_SKIPPED, error_message="Lead exited campaign")

    if request.headers.get("HX-Request"):
        enrolled = (
            campaign.leads
            .select_related("lead", "lead__company", "enrolled_by")
            .order_by("-enrolled_at")
        )
        return render(request, "campaigns/partials/enrolled_list.html", {
            "campaign": campaign, "enrolled": enrolled,
        })
    return redirect("campaigns:campaign_detail", campaign_id=campaign_id)


# ── Tracking pixel ────────────────────────────────────────────────────────────

def campaign_pixel(request, send_id):
    try:
        cs = CampaignSend.objects.filter(
            pk=send_id, status__in=[CampaignSend.STATUS_SENT, CampaignSend.STATUS_OPENED]
        ).first()
        if cs:
            new_count = (cs.open_count or 0) + 1
            CampaignSend.objects.filter(pk=send_id).update(
                status=CampaignSend.STATUS_OPENED,
                opened_at=timezone.now(),
                open_count=new_count,
            )
            if cs.action_id:
                action = Action.objects.filter(pk=cs.action_id).first()
                if action:
                    meta = dict(action.metadata)
                    meta["email_status"] = "opened"
                    meta["open_count"]   = new_count
                    meta["opened_at"]    = timezone.now().isoformat()
                    Action.objects.filter(pk=cs.action_id).update(metadata=meta)
    except Exception:
        pass
    return HttpResponse(
        _PIXEL_GIF,
        content_type="image/gif",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


# ── Email Templates ───────────────────────────────────────────────────────────

@require_http_methods(["GET", "POST"])
def email_template_list(request):
    """GET — template manager page. POST — create new template."""
    if request.method == "POST":
        name    = request.POST.get("name", "").strip()
        subject = request.POST.get("subject", "").strip()
        body    = request.POST.get("body", "").strip()
        errors  = {}
        if not name:    errors["name"]    = "Template name is required."
        if not subject: errors["subject"] = "Subject is required."
        if not body:    errors["body"]    = "Body is required."
        if not errors:
            EmailTemplate.objects.create(
                name=name, subject=subject, body=body,
                created_by=_db_user(request),
            )
            return redirect("campaigns:email_template_list")
        templates = EmailTemplate.objects.all()
        return render(request, "campaigns/email_templates.html", {
            "templates": templates, "errors": errors, "post": request.POST,
            "sailor_user": _sailor_user(request),
        })

    templates = EmailTemplate.objects.all()
    return render(request, "campaigns/email_templates.html", {
        "templates":   templates,
        "sailor_user": _sailor_user(request),
    })


@require_POST
def email_template_delete(request, template_id):
    tmpl = get_object_or_404(EmailTemplate, pk=template_id)
    tmpl.delete()
    return redirect("campaigns:email_template_list")


def email_templates_json(request):
    """Return all templates as JSON for JS auto-fill."""
    from django.http import JsonResponse
    templates = list(EmailTemplate.objects.values("id", "name", "subject", "body"))
    return JsonResponse({"templates": templates})


# ── Bulk activity log ─────────────────────────────────────────────────────────

@require_POST
def campaign_bulk_log(request, campaign_id):
    """
    Log an activity against ALL enrolled active leads in the campaign.
    Creates individual Action rows per lead — each lead's history stays clean.
    """
    campaign       = get_object_or_404(Campaign, pk=campaign_id)
    action_type_id = request.POST.get("action_type_id", "").strip()
    note           = request.POST.get("note", "").strip()

    if not action_type_id:
        return redirect("campaigns:campaign_detail", campaign_id=campaign_id)

    action_type = get_object_or_404(ActionType, pk=action_type_id)
    db_user     = _db_user(request)
    now         = timezone.now()

    enrolled_leads = (
        campaign.leads
        .filter(status__in=[CampaignLead.STATUS_ACTIVE, CampaignLead.STATUS_COMPLETED])
        .select_related("lead")
    )
    for cl in enrolled_leads:
        Action.objects.create(
            lead=cl.lead,
            action_type=action_type,
            performed_by=db_user,
            performed_at=now,
            metadata={
                "note": note,
                "campaign_id":   str(campaign.pk),
                "campaign_name": campaign.name,
                "bulk_logged":   True,
            },
        )

    return redirect("campaigns:campaign_detail", campaign_id=campaign_id)
