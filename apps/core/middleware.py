"""
SailorAuthMiddleware — session-based auth gate.

Redirects any unauthenticated request to /login/ unless the path is
on the public allow-list below.
"""

from django.shortcuts import redirect

# Paths that are accessible without being logged in
_PUBLIC_PREFIXES = (
    "/login/",
    "/auth/redirect/",
    "/auth/callback/",
    "/auth/logout/",
    "/manifest.webmanifest",
    "/service-worker.js",
    "/static/",
    "/admin/",          # Django admin has its own auth
    "/favicon.ico",
    "/email-pixel/",       # Pipeline email tracking pixel
    "/campaigns/pixel/",   # Campaign email tracking pixel
)

# Media sub-paths safe to serve publicly (avatars, logos, etc.)
# /media/imports/ is intentionally NOT listed — raw lead data, auth-gated.
_PUBLIC_MEDIA_PREFIXES = (
    "/media/avatars/",
    "/media/logos/",
    "/media/public/",
)

# Paths that are safe to redirect back to after login (internal only)
_SAFE_NEXT_PREFIXES = (
    "/pipeline/",
    "/dashboard/",
    "/imports/",
    "/leads/",
    "/call/",
)


class SailorAuthMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path_info

        # Allow public paths through unconditionally
        if any(path.startswith(prefix) for prefix in _PUBLIC_PREFIXES):
            return self.get_response(request)

        # Whitelist specific public media sub-dirs; gate everything else under /media/
        if path.startswith("/media/"):
            if any(path.startswith(p) for p in _PUBLIC_MEDIA_PREFIXES):
                return self.get_response(request)
            if not request.session.get("sailor_user"):
                return redirect("/login/")
            return self.get_response(request)

        # Require session auth for everything else
        if not request.session.get("sailor_user"):
            if any(path.startswith(p) for p in _SAFE_NEXT_PREFIXES):
                request.session["login_next"] = path
            return redirect("/login/")

        return self.get_response(request)
