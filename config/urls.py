"""
Root URL configuration.
All app-level routes are namespaced under /api/v1/.
"""

from django.contrib import admin
from django.urls import path, include
from django.views.generic import RedirectView, base
from django.http import JsonResponse
from django.conf import settings
from django.conf.urls.static import static

from apps.core import auth_views, pwa_views

urlpatterns = [
    # Root → pipeline board
    path("", RedirectView.as_view(url="/pipeline/", permanent=False)),

    path("admin/", admin.site.urls),

    # ── Azure AD SSO ──────────────────────────────────────────────────────────
    path("login/",         auth_views.login_page,    name="login"),
    path("auth/redirect/", auth_views.azure_redirect, name="auth_redirect"),
    path("auth/callback/", auth_views.auth_callback,  name="auth_callback"),
    path("auth/logout/",   auth_views.logout,          name="auth_logout"),

    # PWA / call handoff
    path("manifest.webmanifest", pwa_views.manifest, name="pwa_manifest"),
    path("service-worker.js",    pwa_views.service_worker, name="service_worker"),
    path("call/setup/",          pwa_views.setup_calling, name="call_setup"),
    path("call/register-device/", pwa_views.register_device, name="call_register_device"),
    path("call/send/<uuid:lead_id>/", pwa_views.send_call_to_phone, name="call_send_to_phone"),
    path("call/launch/",         pwa_views.launch_call, name="call_launch"),

    # ── UI routes (Django templates + HTMX) ───────────────────────────────────
    path("", include("apps.pipeline.urls", namespace="pipeline")),
    path("", include("apps.imports.urls",   namespace="imports")),
    path("", include("apps.campaigns.urls", namespace="campaigns")),

    # ── REST API routes (future Angular / integrations) ───────────────────────
    path("api/v1/", include("apps.leads.urls",      namespace="leads")),
    path("api/v1/", include("apps.actions.urls",    namespace="actions")),
    path("api/v1/", include("apps.users.urls",      namespace="users")),
    path("api/v1/", include("apps.ai_layer.urls",   namespace="ai_layer")),
    path("api/v1/", include("apps.automation.urls", namespace="automation")),

    # ── Silence Chrome DevTools auto-probe (avoids 404 noise in logs) ─────────
    path(".well-known/appspecific/com.chrome.devtools.json",
         lambda r: JsonResponse({}), name="chrome_devtools_probe"),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
