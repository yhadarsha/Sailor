"""
Microsoft Graph email helper.

Usage:
    from apps.core.graph_email import send_graph_email, GraphEmailError

    try:
        send_graph_email(
            access_token=token,
            to_email="lead@example.com",
            subject="Hello!",
            body_html="<p>Hi there</p>",
        )
    except GraphEmailError as exc:
        ...
"""

import logging
import time
import requests

logger = logging.getLogger(__name__)

GRAPH_SEND_MAIL_URL = "https://graph.microsoft.com/v1.0/me/sendMail"


class GraphEmailError(Exception):
    """Raised when the Graph API call fails."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        is_throttled: bool = False,
        retry_after: int | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        # True when the failure looks like Microsoft rate-limiting / restricting
        # the sending mailbox (429, or a 403 whose body mentions quota/blocked/
        # restricted) rather than an ordinary per-message failure (bad address,
        # expired token, etc.). Callers should stop sending immediately when
        # this is True instead of continuing through the rest of the batch.
        self.is_throttled = is_throttled
        self.retry_after = retry_after


_THROTTLE_HINTS = (
    "throttl", "quota", "blocked", "restrict", "exceeded", "toomanyrequests",
)


def send_graph_email(
    *,
    access_token: str,
    to_email: str,
    subject: str,
    body_html: str,
    attachments: list[dict] | None = None,
    reply_to_email: str | None = None,
    save_to_sent: bool = True,
    request_delivery_receipt: bool = False,
    request_read_receipt: bool = False,
) -> None:
    """
    Send an email as the signed-in user via Microsoft Graph.

    Args:
        access_token:             A valid delegated-permission bearer token with Mail.Send.
        to_email:                 Recipient email address.
        subject:                  Email subject line.
        body_html:                HTML body content (may include a tracking pixel).
        attachments:              List of Graph fileAttachment dicts (base64-encoded).
                                  Each item must have: @odata.type, name, contentType,
                                  contentBytes.
        save_to_sent:             Save to Sent Items folder (default True).
        request_delivery_receipt: Ask the recipient server to confirm delivery.
        request_read_receipt:     Ask the recipient to send a read confirmation.
                                  Note: many clients suppress read receipts.

    Raises:
        GraphEmailError: on any API or network failure.
    """
    if not access_token:
        raise GraphEmailError("No access token available. Please re-login.")

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type":  "application/json",
    }

    message: dict = {
        "subject": subject,
        "body": {
            "contentType": "HTML",
            "content":     body_html,
        },
        "toRecipients": [
            {"emailAddress": {"address": to_email}}
        ],
        "isDeliveryReceiptRequested": request_delivery_receipt,
        "isReadReceiptRequested":     request_read_receipt,
    }

    if reply_to_email:
        message["replyTo"] = [{"emailAddress": {"address": reply_to_email}}]

    if attachments:
        message["attachments"] = attachments

    payload = {
        "message":        message,
        "saveToSentItems": save_to_sent,
    }

    # Microsoft Graph throttles sendMail two ways that both need handling:
    # 429 TooManyRequests (always carries a Retry-After header) and 403
    # Forbidden with error code "ApplicationThrottled" (no Retry-After —
    # Microsoft's own guidance is to fall back to a fixed/exponential wait).
    # Both are usually transient — a brief backoff-and-retry clears them
    # without bothering the caller. If it's still throttled after that,
    # surface it as is_throttled=True so the campaign task pauses sending
    # instead of hammering a mailbox that may now be genuinely restricted.
    max_attempts = 2
    last_err_msg = ""
    last_status = None

    for attempt in range(1, max_attempts + 1):
        try:
            resp = requests.post(GRAPH_SEND_MAIL_URL, headers=headers, json=payload, timeout=20)
        except requests.RequestException as exc:
            logger.error("Graph email network error: %s", exc)
            raise GraphEmailError(f"Network error sending email: {exc}") from exc

        if resp.status_code == 202:
            # 202 Accepted — success
            return

        err_code = ""
        try:
            err_body = resp.json()
            err_msg  = err_body.get("error", {}).get("message", resp.text)
            err_code = (err_body.get("error", {}).get("code") or "").lower()
        except Exception:
            err_msg = resp.text or f"HTTP {resp.status_code}"

        logger.error("Graph sendMail failed (%s, attempt %d/%d): %s", resp.status_code, attempt, max_attempts, err_msg)
        last_err_msg, last_status = err_msg, resp.status_code

        if resp.status_code == 401:
            raise GraphEmailError("Session expired. Please re-login to send emails.", resp.status_code)

        if resp.status_code == 413:
            raise GraphEmailError(
                "Payload too large. Reduce attachment sizes and try again.",
                resp.status_code,
            )

        is_429 = resp.status_code == 429
        is_app_throttled = resp.status_code == 403 and "applicationthrottled" in err_code
        hint_throttled = any(hint in err_msg.lower() for hint in _THROTTLE_HINTS)

        if (is_429 or is_app_throttled) and attempt < max_attempts:
            if is_429:
                try:
                    wait = min(int(resp.headers.get("Retry-After", "5")), 60)
                except (TypeError, ValueError):
                    wait = 5
            else:
                wait = 30  # ApplicationThrottled carries no Retry-After — fixed backoff
            logger.warning("Graph throttled this send — backing off %ss before retrying once.", wait)
            time.sleep(wait)
            continue

        if resp.status_code == 429:
            retry_after = None
            try:
                retry_after = int(resp.headers.get("Retry-After", "0")) or None
            except (TypeError, ValueError):
                pass
            raise GraphEmailError(
                f"Rate limited by Microsoft Graph: {err_msg}",
                resp.status_code,
                is_throttled=True,
                retry_after=retry_after,
            )

        if resp.status_code == 403:
            throttled = is_app_throttled or hint_throttled
            raise GraphEmailError(
                "Permission denied. Mail.Send consent may not be granted, or the mailbox "
                f"has been restricted for sending — check Azure app permissions. ({err_msg})",
                resp.status_code,
                is_throttled=throttled,
            )

        # Catch-all: some tenants return 400/503 with a body that says the
        # account is restricted/throttled rather than using 429/403 — sniff
        # the message too.
        raise GraphEmailError(
            f"Failed to send email: {err_msg}", resp.status_code, is_throttled=hint_throttled
        )

    # Exhausted retries while still being throttled.
    raise GraphEmailError(
        f"Still rate-limited/throttled after {max_attempts} attempts: {last_err_msg}",
        last_status,
        is_throttled=True,
    )

