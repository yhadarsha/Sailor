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
import requests

logger = logging.getLogger(__name__)

GRAPH_SEND_MAIL_URL = "https://graph.microsoft.com/v1.0/me/sendMail"


class GraphEmailError(Exception):
    """Raised when the Graph API call fails."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


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

    try:
        resp = requests.post(GRAPH_SEND_MAIL_URL, headers=headers, json=payload, timeout=20)
    except requests.RequestException as exc:
        logger.error("Graph email network error: %s", exc)
        raise GraphEmailError(f"Network error sending email: {exc}") from exc

    if resp.status_code == 202:
        # 202 Accepted — success
        return

    try:
        err_body = resp.json()
        err_msg  = err_body.get("error", {}).get("message", resp.text)
    except Exception:
        err_msg = resp.text or f"HTTP {resp.status_code}"

    logger.error("Graph sendMail failed (%s): %s", resp.status_code, err_msg)

    if resp.status_code == 401:
        raise GraphEmailError("Session expired. Please re-login to send emails.", resp.status_code)
    if resp.status_code == 403:
        raise GraphEmailError(
            "Permission denied. Mail.Send consent may not be granted — check Azure app permissions.",
            resp.status_code,
        )
    if resp.status_code == 413:
        raise GraphEmailError(
            "Payload too large. Reduce attachment sizes and try again.",
            resp.status_code,
        )

    raise GraphEmailError(f"Failed to send email: {err_msg}", resp.status_code)

