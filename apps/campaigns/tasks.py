"""
Celery tasks for the campaigns app.

process_due_campaign_sends — runs every 5 minutes, sends all overdue queued emails
                             across all active campaigns automatically.
"""

import logging
import time

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)

BATCH_SIZE = 20   # max emails per task run (respect O365 30/min rate limit)
SEND_DELAY  = 2   # seconds between sends


@shared_task(name="apps.campaigns.tasks.process_due_campaign_sends", bind=True, max_retries=3)
def process_due_campaign_sends(self):
    """
    Find all queued CampaignSend rows whose scheduled_for <= now and send them.
    Runs every 5 minutes via Celery Beat.

    Uses the campaign creator's stored MSAL token to authenticate Graph API calls.
    Falls back gracefully if the token is expired — the send will retry next cycle.
    """
    from apps.campaigns.models import CampaignSend, CampaignLead, Campaign
    from apps.actions.models import Action, ActionType
    from apps.core.graph_email import send_graph_email, GraphEmailError

    now = timezone.now()

    due_sends = (
        CampaignSend.objects
        .filter(
            status=CampaignSend.STATUS_QUEUED,
            scheduled_for__lte=now,
            campaign_lead__status__in=[CampaignLead.STATUS_ACTIVE, CampaignLead.STATUS_COMPLETED],
        )
        .select_related(
            "campaign_lead__campaign__created_by",
            "campaign_lead__lead__company",
            "step",
        )
        .order_by("scheduled_for")[:BATCH_SIZE]
    )

    sent = failed = skipped = 0
    email_type = ActionType.objects.filter(category="email").first()

    for cs in due_sends:
        lead     = cs.campaign_lead.lead
        campaign = cs.campaign_lead.campaign

        if not lead.email:
            CampaignSend.objects.filter(pk=cs.pk).update(
                status=CampaignSend.STATUS_SKIPPED, error_message="No email address"
            )
            skipped += 1
            continue

        # Get token for campaign creator from their stored MSAL cache
        access_token = _get_token_for_user(campaign.created_by)
        if not access_token:
            logger.warning(
                "Campaign %s: no valid token for user %s — skipping send for lead %s",
                campaign.name, campaign.created_by, lead.email,
            )
            skipped += 1
            continue

        sender_name = campaign.created_by.display_name if campaign.created_by else "The Team"
        pixel_url   = f"https://{_get_domain()}/campaigns/pixel/{cs.pk}/"

        from apps.campaigns.views import _render_template
        subject      = _render_template(cs.step.subject_template, lead, sender_name)
        body_rendered = _render_template(cs.step.body_html_template, lead, sender_name, pixel_url)

        action = None
        if email_type:
            action = Action.objects.create(
                lead=lead,
                action_type=email_type,
                performed_by=campaign.created_by,
                performed_at=now,
                metadata={
                    "note":           f"[Campaign: {campaign.name}] {subject}",
                    "email_subject":  subject,
                    "email_body":     body_rendered,
                    "email_to":       lead.email,
                    "email_status":   "sent",
                    "campaign_id":    str(campaign.pk),
                    "campaign_name":  campaign.name,
                    "campaign_step":  cs.step.step_number,
                    "has_attachment": False,
                },
            )

        try:
            send_graph_email(
                access_token=access_token,
                to_email=lead.email,
                subject=subject,
                body_html=body_rendered,
            )
            CampaignSend.objects.filter(pk=cs.pk).update(
                status=CampaignSend.STATUS_SENT,
                sent_at=now,
                action_id=action.pk if action else None,
            )
            CampaignLead.objects.filter(pk=cs.campaign_lead_id).update(
                current_step=cs.step.step_number,
                status=CampaignLead.STATUS_ACTIVE,
            )
            sent += 1
            logger.info("Campaign %s: sent to %s", campaign.name, lead.email)
        except GraphEmailError as exc:
            CampaignSend.objects.filter(pk=cs.pk).update(
                status=CampaignSend.STATUS_FAILED, error_message=str(exc)
            )
            if action:
                from apps.actions.models import Action as ActionModel
                meta = dict(action.metadata)
                meta["email_status"] = "failed"
                meta["error"] = str(exc)
                ActionModel.objects.filter(pk=action.pk).update(metadata=meta)
            failed += 1
            logger.error("Campaign %s: failed to send to %s — %s", campaign.name, lead.email, exc)

        time.sleep(SEND_DELAY)

    # Mark leads as completed if all their sends are done
    processed_lead_ids = set(cs.campaign_lead_id for cs in due_sends)
    for cl_id in processed_lead_ids:
        try:
            cl = CampaignLead.objects.get(pk=cl_id)
            if not cl.sends.filter(status=CampaignSend.STATUS_QUEUED).exists():
                CampaignLead.objects.filter(pk=cl_id).update(status=CampaignLead.STATUS_COMPLETED)
        except Exception:
            pass

    logger.info(
        "process_due_campaign_sends: sent=%d failed=%d skipped=%d", sent, failed, skipped
    )
    return {"sent": sent, "failed": failed, "skipped": skipped}


def _acquire_silent(cache):
    """Shared helper: build an MSAL client around a deserialized cache and
    try a silent token refresh. Returns the MSAL result dict, or None."""
    import msal
    from django.conf import settings

    cca = msal.ConfidentialClientApplication(
        settings.AZURE_AD_CLIENT_ID,
        authority=settings.AZURE_AD_AUTHORITY,
        client_credential=settings.AZURE_AD_CLIENT_SECRET,
        token_cache=cache,
    )
    accounts = cca.get_accounts()
    if not accounts:
        return None
    return cca.acquire_token_silent(settings.AZURE_AD_SCOPES, account=accounts[0])


def _get_token_from_persistent_store(user):
    """
    PRIMARY path: refresh a Graph access token from the UserMailToken table
    (encrypted MSAL cache, independent of any web session). This is what
    lets a scheduled send go out even if the user is on leave and hasn't
    logged in — as long as their Microsoft refresh token (~90-day rolling
    window with offline_access) is still valid.

    Returns None if there's no persistent record yet (e.g. user hasn't
    logged in since this feature shipped) — caller falls back to the
    session-scan method in that case.
    """
    import msal
    from apps.users.models import UserMailToken
    from apps.core.crypto import decrypt_str, encrypt_str

    token_row = UserMailToken.objects.filter(user=user).first()
    if not token_row or not token_row.encrypted_cache:
        return None

    serialized = decrypt_str(token_row.encrypted_cache)
    if not serialized:
        return None

    cache = msal.SerializableTokenCache()
    cache.deserialize(serialized)

    result = _acquire_silent(cache)

    if cache.has_state_changed:
        token_row.encrypted_cache = encrypt_str(cache.serialize())
        token_row.save(update_fields=["encrypted_cache", "updated_at"])

    if result and "access_token" in result:
        return result["access_token"]
    return None


def _get_token_from_session(user):
    """
    FALLBACK path (pre-existing behaviour): scan active Django sessions for
    one belonging to this user and refresh from its embedded MSAL cache.
    Kept so users who logged in before the persistent-token feature shipped
    keep working today, without needing to immediately re-login — but note
    this still expires with the session (8h / browser close), so it's only
    a bridge until everyone has logged in at least once post-rollout.
    """
    import msal
    from django.contrib.sessions.backends.db import SessionStore
    from django.contrib.sessions.models import Session
    from django.utils import timezone as tz

    active_sessions = Session.objects.filter(expire_date__gt=tz.now())
    for session_obj in active_sessions:
        data = session_obj.get_decoded()
        sailor = data.get("sailor_user", {})
        if sailor.get("email", "").lower() != user.email.lower():
            continue
        cache_data = data.get("msal_token_cache")
        if not cache_data:
            continue
        cache = msal.SerializableTokenCache()
        cache.deserialize(cache_data)

        result = _acquire_silent(cache)
        if result and "access_token" in result:
            data["msal_token_cache"] = cache.serialize()
            store = SessionStore(session_key=session_obj.session_key)
            store.update(data)
            store.save()
            return result["access_token"]
    return None


def _get_token_for_user(user):
    """
    Retrieve a valid Graph access token for the given user.

    Tries the persistent UserMailToken store first (works regardless of
    login state — see that model's docstring for why this exists), and
    falls back to scanning active web sessions for users who haven't
    logged in since this feature shipped.
    """
    if not user:
        return None
    try:
        token = _get_token_from_persistent_store(user)
        if token:
            return token
        return _get_token_from_session(user)
    except Exception as exc:
        logger.warning("_get_token_for_user failed: %s", exc)
    return None


def _get_domain():
    """Return the site domain for building pixel URLs."""
    try:
        from django.conf import settings
        hosts = getattr(settings, "ALLOWED_HOSTS", [])
        for h in hosts:
            if h not in ("localhost", "127.0.0.1", "0.0.0.0", "*"):
                return h
        return "localhost:8000"
    except Exception:
        return "localhost:8000"
