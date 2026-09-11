"""
Celery tasks for the campaigns app.

process_due_campaign_sends — runs every 5 minutes. Claims due CampaignSend
                              rows and schedules each as its own Celery task
                              (send_single_campaign_email) with a per-user
                              countdown. Never sleeps and never calls Graph
                              itself — see process_due_campaign_sends'
                              docstring for why this is split out this way.

send_single_campaign_email  — sends exactly one email. This is what actually
                              calls Microsoft Graph.
"""

import logging
import random

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

BATCH_SIZE = 20   # max emails claimed per dispatcher run (respect O365 30/min rate limit)

# Randomized delay between sends for the same mailbox. A fixed cadence (e.g.
# exactly 2s every time) is itself a bulk-sending signature that mail
# providers' abuse detection looks for, on top of raw volume — so jitter the
# gap instead of using a constant. ~1 minute spacing means a 30-email
# campaign takes ~30 minutes to fully send, which reads as a person working
# through a list, not a script. Override via settings if needed.
MIN_SEND_DELAY = getattr(settings, "CAMPAIGN_MIN_SEND_DELAY", 120)   # 2 min
MAX_SEND_DELAY = getattr(settings, "CAMPAIGN_MAX_SEND_DELAY", 300)   # 5 min

# Hard ceiling on how many campaign emails a single mailbox will send in a
# rolling 24h window, independent of how many are "due". This is the agreed
# steady-state target (≈30/campaign/day) — deliberately tiny next to
# Microsoft's technical throttle (30/min, 10k recipients/day) and even next
# to the ~1,000/day Microsoft itself uses for "non-relationship" (cold)
# recipients. This is the *final* cap a mailbox ramps up to — see
# WARMUP_SCHEDULE below.
MAX_SENDS_PER_USER_PER_DAY = getattr(settings, "CAMPAIGN_MAX_SENDS_PER_USER_PER_DAY", 30)

# Warm-up ramp: (max_days_of_sending_history, daily_cap_for_that_stage).
# Kept short on purpose — a multi-week ramp is overkill for a target volume
# this small. One conservative day (half volume) before the mailbox is
# trusted with the full ~30/day target. Days are counted from the mailbox's
# first campaign send, not the calendar date, so each fresh sending mailbox
# (e.g. right after being unblocked) restarts the ramp. Override via settings
# if a mailbox already has an established sending reputation and the org
# wants to skip the ramp entirely.
WARMUP_SCHEDULE = getattr(settings, "CAMPAIGN_WARMUP_SCHEDULE", [
    (1, 15),    # day 0-1: max 15/day
    # after day 1: MAX_SENDS_PER_USER_PER_DAY (30) applies
])

# If this many *actual send attempts* in a row fail for the same user (in
# real chronological send order, via CampaignSend.attempted_at — not just
# within a single dispatcher run, since sends are now spread across many
# independent task executions), pause that user's active campaigns rather
# than continuing to blast a queue into what's likely an active block.
MAX_CONSECUTIVE_FAILURES = getattr(settings, "CAMPAIGN_MAX_CONSECUTIVE_FAILURES", 3)


def _count_sent_last_24h(user, now):
    from apps.campaigns.models import CampaignSend

    window_start = now - timezone.timedelta(hours=24)
    return CampaignSend.objects.filter(
        status=CampaignSend.STATUS_SENT,
        sent_at__gte=window_start,
        campaign_lead__campaign__created_by=user,
    ).count()


def _resolve_daily_cap(user, now):
    """Resolve today's send cap for this user from the warm-up schedule,
    based on how long they've actually been sending campaign emails — not
    the calendar date. A mailbox with no send history yet starts at the
    first (lowest) tier."""
    from apps.campaigns.models import CampaignSend

    first_sent = CampaignSend.objects.filter(
        status=CampaignSend.STATUS_SENT,
        campaign_lead__campaign__created_by=user,
    ).order_by("sent_at").values_list("sent_at", flat=True).first()

    days_active = 0 if first_sent is None else (now.date() - first_sent.date()).days

    cap = MAX_SENDS_PER_USER_PER_DAY
    for max_days, tier_cap in WARMUP_SCHEDULE:
        if days_active <= max_days:
            cap = tier_cap
            break
    return cap


def _remaining_today(user, now):
    return _resolve_daily_cap(user, now) - _count_sent_last_24h(user, now)


def _recent_consecutive_failures(user, lookback=None):
    """How many of this user's most recent *attempted* sends — in actual
    chronological send order via attempted_at, across all campaigns and all
    task runs — failed in a row, most recent first. Stops at the first
    non-failure. attempted_at (not created_at/scheduled_for) is what makes
    this reflect real send order rather than queue order."""
    from apps.campaigns.models import CampaignSend

    lookback = lookback or MAX_CONSECUTIVE_FAILURES
    recent_statuses = list(
        CampaignSend.objects.filter(
            campaign_lead__campaign__created_by=user,
            attempted_at__isnull=False,
        )
        .order_by("-attempted_at")
        .values_list("status", flat=True)[:lookback]
    )
    streak = 0
    for status in recent_statuses:
        if status == "failed":
            streak += 1
        else:
            break
    return streak


def _halt_user(user, reason):
    from apps.campaigns.models import Campaign

    Campaign.objects.filter(
        created_by=user, status=Campaign.STATUS_ACTIVE
    ).update(status=Campaign.STATUS_PAUSED)
    logger.critical(
        "Mailbox for user %s appears throttled/blocked (%s) — paused their "
        "active campaigns. Resuming is manual only — no auto-resume.",
        user, reason,
    )


def _maybe_complete_lead(campaign_lead_id):
    from apps.campaigns.models import CampaignLead, CampaignSend

    still_pending = CampaignSend.objects.filter(
        campaign_lead_id=campaign_lead_id,
        status__in=[CampaignSend.STATUS_QUEUED, CampaignSend.STATUS_SCHEDULED],
    ).exists()
    if not still_pending:
        CampaignLead.objects.filter(pk=campaign_lead_id).update(
            status=CampaignLead.STATUS_COMPLETED
        )


def dispatch_campaign_sends(queryset, batch_size=None, advance_next_step=False):
    """
    Claim up to `batch_size` rows from `queryset` (must be a CampaignSend
    queryset filtered to STATUS_QUEUED rows, with whatever extra scoping the
    caller needs — e.g. a single campaign or step) and hand each one to
    send_single_campaign_email as its own Celery task with a per-user
    countdown, after checking the daily-cap/warm-up slot for that user.

    This is the ONE place that claims rows and applies jitter/cap/warm-up —
    every send path (the automatic 5-minute dispatcher below, and the
    manual "Send Now" views) must go through this rather than looping over
    sends and calling send_graph_email directly. That loop-and-sleep pattern
    is exactly what caused the original block: no jitter, no daily ceiling,
    and it also blocks whatever process runs it (a Celery worker for the
    automatic path, a web request thread for the manual ones) for the
    entire duration.

    Uses SELECT ... FOR UPDATE SKIP LOCKED so two concurrent callers (e.g.
    the periodic dispatcher and someone clicking "Send Now" at the same
    moment) can never claim — and therefore double-send — the same row.

    Returns (scheduled_count, skipped_count).
    """
    from apps.campaigns.models import CampaignSend

    now = timezone.now()
    batch_size = batch_size or BATCH_SIZE
    scheduled = skipped = 0

    sent_today_cache = {}    # user_id -> sends in the last 24h (one query/user/call)
    cap_cache = {}           # user_id -> resolved warm-up cap for today
    scheduled_this_run = {}  # user_id -> slots reserved so far this call
    next_countdown = {}      # user_id -> countdown (seconds) for this user's next send
    accepted = []            # (str(pk), countdown_seconds)

    def _remaining(user):
        if user.pk not in sent_today_cache:
            sent_today_cache[user.pk] = _count_sent_last_24h(user, now)
        if user.pk not in cap_cache:
            cap_cache[user.pk] = _resolve_daily_cap(user, now)
        used = sent_today_cache[user.pk] + scheduled_this_run.get(user.pk, 0)
        return cap_cache[user.pk] - used

    with transaction.atomic():
        due_sends = list(
            queryset.select_for_update(skip_locked=True).order_by("scheduled_for")[:batch_size]
        )

        for cs in due_sends:
            lead = cs.campaign_lead.lead
            campaign = cs.campaign_lead.campaign
            user = campaign.created_by

            if not lead.email:
                CampaignSend.objects.filter(pk=cs.pk).update(
                    status=CampaignSend.STATUS_SKIPPED, error_message="No email address"
                )
                skipped += 1
                continue

            if user and _remaining(user) <= 0:
                logger.info(
                    "Campaign %s: daily send cap reached for user %s — leaving lead %s queued",
                    campaign.name, user, lead.email,
                )
                skipped += 1
                continue

            countdown = next_countdown.get(user.pk, 0) if user else 0

            CampaignSend.objects.filter(pk=cs.pk).update(status=CampaignSend.STATUS_SCHEDULED)

            if user:
                scheduled_this_run[user.pk] = scheduled_this_run.get(user.pk, 0) + 1
                next_countdown[user.pk] = countdown + random.uniform(MIN_SEND_DELAY, MAX_SEND_DELAY)

            accepted.append((str(cs.pk), countdown))
            scheduled += 1

    # Hand off to per-send tasks only after the claiming transaction (and its
    # row locks) has committed — apply_async should never happen mid-lock.
    for send_id, countdown in accepted:
        send_single_campaign_email.apply_async(
            args=[send_id, advance_next_step], countdown=countdown
        )

    return scheduled, skipped


@shared_task(name="apps.campaigns.tasks.process_due_campaign_sends", bind=True, max_retries=3)
def process_due_campaign_sends(self):
    """
    Dispatcher — runs every 5 minutes via Celery Beat. Finds every queued,
    due CampaignSend across all active campaigns and routes it through
    dispatch_campaign_sends().

    This task never calls Microsoft Graph and never sleeps: it claims and
    returns, typically in well under a second regardless of batch size. The
    previous design slept 50-70s *inside this task* between every send in a
    batch, which could tie up a single Celery worker process for 15-25+
    minutes per run and risked task pile-ups if Beat fired the next cycle
    before a run finished. Splitting send-scheduling (cheap, here) from
    send-execution (network-bound, in send_single_campaign_email) fixes that.
    """
    from apps.campaigns.models import CampaignSend, CampaignLead, Campaign

    now = timezone.now()
    qs = CampaignSend.objects.filter(
        status=CampaignSend.STATUS_QUEUED,
        scheduled_for__lte=now,
        campaign_lead__status__in=[CampaignLead.STATUS_ACTIVE, CampaignLead.STATUS_COMPLETED],
        # Exclude paused campaigns — a mailbox that just got auto-paused for
        # throttling/blocking must not have its still-queued sends picked
        # right back up next cycle.
        campaign_lead__campaign__status=Campaign.STATUS_ACTIVE,
    ).select_related("campaign_lead__campaign__created_by", "campaign_lead__lead")

    scheduled, skipped = dispatch_campaign_sends(qs)

    logger.info("process_due_campaign_sends: scheduled=%d skipped=%d", scheduled, skipped)
    return {"scheduled": scheduled, "skipped": skipped}


@shared_task(name="apps.campaigns.tasks.send_single_campaign_email", bind=True, max_retries=0)
def send_single_campaign_email(self, campaign_send_id, advance_next_step=False):
    """
    Send exactly one campaign email. Scheduled by dispatch_campaign_sends via
    Celery's countdown — this is what actually calls Microsoft Graph, and it
    always runs as its own task so the caller (the periodic dispatcher, or a
    manual "Send Now" view) never blocks on it.

    advance_next_step: if True and the send succeeds, automatically create
    the next sequence step's CampaignSend for this lead (picking a random
    variant if the next step has more than one). This mirrors the
    pre-existing behaviour of the manual "Send Now" button, which both sends
    due emails AND advances multi-step sequences in one action. The
    automatic 5-minute dispatcher does NOT set this — it only ever sends
    rows that already exist; sequence advancement there is unchanged from
    before this refactor.
    """
    from apps.campaigns.models import CampaignSend, CampaignLead, Campaign
    from apps.actions.models import Action, ActionType
    from apps.core.graph_email import send_graph_email, GraphEmailError

    now = timezone.now()

    with transaction.atomic():
        try:
            cs = CampaignSend.objects.select_for_update().select_related(
                "campaign_lead__campaign__created_by",
                "campaign_lead__lead__company",
                "step",
            ).get(pk=campaign_send_id)
        except CampaignSend.DoesNotExist:
            logger.warning("send_single_campaign_email: %s no longer exists", campaign_send_id)
            return {"status": "gone"}

        if cs.status != CampaignSend.STATUS_SCHEDULED:
            # Already handled by something else (shouldn't normally happen
            # given the dispatcher's locking, but cheap to guard against).
            return {"status": "skipped", "reason": f"unexpected status {cs.status}"}

        lead = cs.campaign_lead.lead
        campaign = cs.campaign_lead.campaign
        user = campaign.created_by

        # Re-check the campaign is still active — it may have been paused
        # (by the circuit breaker below, or manually) in the gap between
        # dispatch and this countdown firing. Revert to queued: per the
        # user's preference there is no auto-resume, so this just sits
        # there until a human manually reactivates the campaign, at which
        # point the next dispatcher run picks it up normally.
        if campaign.status != Campaign.STATUS_ACTIVE:
            CampaignSend.objects.filter(pk=cs.pk).update(status=CampaignSend.STATUS_QUEUED)
            return {"status": "skipped", "reason": "campaign no longer active"}

        if user and _remaining_today(user, now) <= 0:
            CampaignSend.objects.filter(pk=cs.pk).update(status=CampaignSend.STATUS_QUEUED)
            return {"status": "skipped", "reason": "daily cap reached"}

        access_token = _get_token_for_user(user)
        if not access_token:
            CampaignSend.objects.filter(pk=cs.pk).update(status=CampaignSend.STATUS_QUEUED)
            logger.warning(
                "Campaign %s: no valid token for user %s — requeued lead %s",
                campaign.name, user, lead.email,
            )
            return {"status": "skipped", "reason": "no token"}

        sender_name = user.display_name if user else "The Team"
        pixel_url = f"https://{_get_domain()}/campaigns/pixel/{cs.pk}/"

        from apps.campaigns.views import _render_template
        subject = _render_template(cs.step.subject_template, lead, sender_name)
        body_rendered = _render_template(cs.step.body_html_template, lead, sender_name, pixel_url)

        email_type = ActionType.objects.filter(category="email").first()
        action = None
        if email_type:
            action = Action.objects.create(
                lead=lead,
                action_type=email_type,
                performed_by=user,
                performed_at=now,
                metadata={
                    "note": f"[Campaign: {campaign.name}] {subject}",
                    "email_subject": subject,
                    "email_body": body_rendered,
                    "email_to": lead.email,
                    "email_status": "sent",
                    "campaign_id": str(campaign.pk),
                    "campaign_name": campaign.name,
                    "campaign_step": cs.step.step_number,
                    "has_attachment": False,
                },
            )

    # The Graph call deliberately happens outside the transaction/row-lock —
    # it's a network call and shouldn't hold a DB lock for however long it
    # (and its own retry/backoff inside send_graph_email) takes.
    try:
        send_graph_email(
            access_token=access_token,
            to_email=lead.email,
            subject=subject,
            body_html=body_rendered,
        )
    except GraphEmailError as exc:
        attempted = timezone.now()
        CampaignSend.objects.filter(pk=cs.pk).update(
            status=CampaignSend.STATUS_FAILED, error_message=str(exc), attempted_at=attempted,
        )
        if action:
            meta = dict(action.metadata)
            meta["email_status"] = "failed"
            meta["error"] = str(exc)
            Action.objects.filter(pk=action.pk).update(metadata=meta)
        logger.error("Campaign %s: failed to send to %s — %s", campaign.name, lead.email, exc)

        if user:
            streak = _recent_consecutive_failures(user)
            if exc.is_throttled:
                _halt_user(user, f"Graph returned a throttled/blocked response: {exc}")
            elif streak >= MAX_CONSECUTIVE_FAILURES:
                _halt_user(user, f"{streak} consecutive send failures")

        _maybe_complete_lead(cs.campaign_lead_id)
        return {"status": "failed"}

    attempted = timezone.now()
    CampaignSend.objects.filter(pk=cs.pk).update(
        status=CampaignSend.STATUS_SENT, sent_at=attempted, attempted_at=attempted,
        action_id=action.pk if action else None,
    )
    CampaignLead.objects.filter(pk=cs.campaign_lead_id).update(
        current_step=cs.step.step_number, status=CampaignLead.STATUS_ACTIVE,
    )

    if advance_next_step:
        next_step_number = cs.step.step_number + 1
        next_steps = list(campaign.steps.filter(step_number=next_step_number))
        if next_steps:
            next_step = random.choice(next_steps)
            if not CampaignSend.objects.filter(
                campaign_lead_id=cs.campaign_lead_id, step__step_number=next_step_number
            ).exists():
                next_scheduled = next_step.scheduled_at if next_step.scheduled_at else attempted
                CampaignSend.objects.create(
                    campaign_lead_id=cs.campaign_lead_id,
                    step=next_step,
                    variant_label=next_step.variant_label,
                    scheduled_for=next_scheduled,
                )

    _maybe_complete_lead(cs.campaign_lead_id)

    logger.info("Campaign %s: sent to %s", campaign.name, lead.email)
    return {"status": "sent"}


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
