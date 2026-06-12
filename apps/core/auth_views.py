"""
Sailor SSO — Azure AD auth views.

Flow:
  GET  /login/          → login page (org-style: dark bg + card)
  GET  /auth/redirect/  → build Microsoft OAuth URL → redirect
  GET  /auth/callback/  → exchange code, store user in session, → /pipeline/
  GET  /auth/logout/    → clear session → Azure AD logout
"""

import secrets
import logging
from urllib.parse import urlencode

import uuid as _uuid
import msal
import requests
from django.conf import settings
from django.shortcuts import redirect, render
from django.utils import timezone

from apps.users.models import AllowedLogin, User

logger = logging.getLogger(__name__)


def _sync_graph_phone_fields(user: User | None, access_token: str | None) -> None:
    if not user or not access_token:
        return

    headers = {"Authorization": f"Bearer {access_token}"}
    updates = []

    try:
        resp = requests.get(
            "https://graph.microsoft.com/v1.0/me"
            "?$select=mobilePhone,businessPhones",
            headers=headers,
            timeout=10,
        )
        if resp.ok:
            data = resp.json()
            mobile_phone = (data.get("mobilePhone") or "").strip()
            business_phones = data.get("businessPhones") or []
            business_phone = (business_phones[0] if business_phones else "").strip()
            if mobile_phone and user.mobile_phone != mobile_phone:
                user.mobile_phone = mobile_phone
                updates.append("mobile_phone")
            if business_phone and user.business_phone != business_phone:
                user.business_phone = business_phone
                updates.append("business_phone")
        else:
            logger.info("Graph /me phone sync skipped: %s %s", resp.status_code, resp.text[:200])
    except requests.RequestException as exc:
        logger.info("Graph /me phone sync failed: %s", exc)

    try:
        resp = requests.get(
            "https://graph.microsoft.com/v1.0/me/authentication/phoneMethods",
            headers=headers,
            timeout=10,
        )
        if resp.ok:
            methods = resp.json().get("value", [])
            phone = ""
            for method in methods:
                if method.get("phoneType") == "mobile":
                    phone = method.get("phoneNumber") or ""
                    break
            if not phone and methods:
                phone = methods[0].get("phoneNumber") or ""
            phone = phone.strip()
            if phone and user.auth_phone != phone:
                user.auth_phone = phone
                updates.append("auth_phone")
        else:
            logger.info(
                "Graph auth phone sync skipped: %s %s",
                resp.status_code,
                resp.text[:200],
            )
    except requests.RequestException as exc:
        logger.info("Graph auth phone sync failed: %s", exc)

    user.last_synced_at = timezone.now()
    updates.append("last_synced_at")
    user.save(update_fields=list(dict.fromkeys(updates + ["updated_at"])))

# ── Helpers ───────────────────────────────────────────────────────────────────

def _msal_app(token_cache: msal.SerializableTokenCache | None = None) -> msal.ConfidentialClientApplication:
    """Return a fresh MSAL confidential-client app, optionally with a token cache."""
    return msal.ConfidentialClientApplication(
        client_id=settings.AZURE_AD_CLIENT_ID,
        client_credential=settings.AZURE_AD_CLIENT_SECRET,
        authority=settings.AZURE_AD_AUTHORITY,
        token_cache=token_cache,
    )


def get_access_token(request) -> str | None:
    """
    Return a valid access token for the logged-in user, refreshing silently if
    the cached token has expired.  Returns None if no cache or refresh fails
    (caller should surface an error asking the user to re-login).
    """
    cache_data = request.session.get("msal_token_cache")
    if not cache_data:
        return None

    cache = msal.SerializableTokenCache()
    cache.deserialize(cache_data)

    app = _msal_app(token_cache=cache)
    accounts = app.get_accounts()
    if not accounts:
        return None

    result = app.acquire_token_silent(
        scopes=settings.AZURE_AD_SCOPES,
        account=accounts[0],
    )

    # Persist refreshed cache back into session
    if cache.has_state_changed:
        request.session["msal_token_cache"] = cache.serialize()
        request.session.modified = True

    if result and "access_token" in result:
        return result["access_token"]
    return None


# ── Views ─────────────────────────────────────────────────────────────────────

def login_page(request):
    """Render the org-style login page (no username/password — single SSO button)."""
    if request.session.get("sailor_user"):
        return redirect("/pipeline/")
    return render(request, "auth/login.html")


def azure_redirect(request):
    """
    Build the Microsoft authorization URL and redirect the browser there.
    Stores a CSRF state token in the session to validate on callback.
    """
    state = secrets.token_urlsafe(24)
    request.session["oauth_state"] = state

    app = _msal_app()
    auth_url = app.get_authorization_request_url(
        scopes=settings.AZURE_AD_SCOPES,
        state=state,
        redirect_uri=settings.AZURE_AD_REDIRECT_URI,
    )
    return redirect(auth_url)


def auth_callback(request):
    """
    Handle the redirect from Microsoft after the user authenticates.
    Exchange the code for tokens, extract identity, upsert User row,
    store a compact auth dict in the session, then redirect to the board.
    """
    # ── Error from Microsoft ──────────────────────────────────────────────────
    error = request.GET.get("error")
    if error:
        description = request.GET.get("error_description", "No details provided.")
        logger.error("Azure AD login error: %s — %s", error, description)
        return render(request, "auth/login.html", {
            "error": f"Login failed: {description}",
        })

    # ── CSRF state check ──────────────────────────────────────────────────────
    returned_state = request.GET.get("state", "")
    saved_state    = request.session.pop("oauth_state", None)
    if not saved_state or returned_state != saved_state:
        logger.warning("OAuth state mismatch — possible CSRF attempt.")
        return render(request, "auth/login.html", {
            "error": "Session mismatch. Please try logging in again.",
        })

    # ── Exchange code for tokens ──────────────────────────────────────────────
    code = request.GET.get("code", "")
    if not code:
        return render(request, "auth/login.html", {
            "error": "Authorization code missing. Please try again.",
        })

    cache = msal.SerializableTokenCache()
    app = _msal_app(token_cache=cache)
    result = app.acquire_token_by_authorization_code(
        code=code,
        scopes=settings.AZURE_AD_SCOPES,
        redirect_uri=settings.AZURE_AD_REDIRECT_URI,
    )

    if "error" in result:
        logger.error("Token exchange error: %s — %s",
                     result.get("error"), result.get("error_description"))
        return render(request, "auth/login.html", {
            "error": "Could not complete sign-in. Please try again.",
        })

    # ── Extract user identity from id_token claims ────────────────────────────
    claims       = result.get("id_token_claims", {})
    oid          = claims.get("oid") or claims.get("sub", "")
    display_name = claims.get("name", "")
    email        = (claims.get("preferred_username") or claims.get("email", "")).lower()

    if not oid:
        logger.error("No 'oid' claim in id_token. Claims: %s", claims)
        return render(request, "auth/login.html", {
            "error": "Could not read your identity from Microsoft. Contact IT.",
        })
    if not email:
        logger.error("No email/preferred_username claim in id_token. Claims: %s", claims)
        return render(request, "auth/login.html", {
            "error": "Could not read your email from Microsoft. Contact IT.",
        })

    allowed_login = AllowedLogin.objects.filter(email__iexact=email, is_active=True).first()
    if not allowed_login:
        logger.warning("Blocked Sailor login for non-allow-listed user: %s", email)
        return render(request, "auth/login.html", {
            "error": "Your Microsoft account is authenticated, but Sailor access is not enabled. Ask the admin to add your email.",
        }, status=403)

    # ── Upsert the local User row (cache of AAD identity) ────────────────────
    try:
        user, created = User.objects.get_or_create(
            id=_uuid.UUID(oid),
            defaults={
                "email": email,
                "display_name": display_name,
                "role": allowed_login.role,
                "is_active": True,
            },
        )
        if not created:
            # Sync display_name / email in case they changed in AAD
            updated = []
            if user.display_name != display_name and display_name:
                user.display_name = display_name
                updated.append("display_name")
            if user.email != email and email:
                user.email = email
                updated.append("email")
            if user.role != allowed_login.role:
                user.role = allowed_login.role
                updated.append("role")
            if updated:
                user.save(update_fields=updated + ["updated_at"])
    except Exception as exc:
        logger.exception("Failed to upsert user oid=%s: %s", oid, exc)
        # Don't block login just because the local DB write failed
        user = None

    _sync_graph_phone_fields(user, result.get("access_token"))

    # ── Persist minimal auth info + MSAL token cache in session ──────────────
    # Cycle session ID to prevent session fixation attacks
    request.session.cycle_key()

    request.session["sailor_user"] = {
        "oid":          oid,
        "email":        email,
        "display_name": display_name,
        "role":         user.role,   # "admin" | "sales" | "viewer"
    }
    request.session["msal_token_cache"] = cache.serialize()

    next_url = request.session.pop("login_next", None) or settings.LOGIN_REDIRECT_URL
    logger.info("User logged in: %s (%s)", display_name, email)
    return redirect(next_url)


def logout(request):
    """
    Clear the Sailor session and redirect to login.
    Does NOT call Microsoft's end_session_endpoint so the AAD SSO cookie
    is preserved — the user stays signed in to O365 but out of Sailor.
    """
    request.session.flush()
    return redirect("login")
