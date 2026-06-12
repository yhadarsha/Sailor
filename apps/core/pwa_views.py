import json
import logging
from urllib.parse import quote

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from apps.actions.models import Action, ActionType
from apps.leads.models import Lead
from apps.users.models import User, UserDevice

logger = logging.getLogger(__name__)


def _current_user(request):
    sailor = request.session.get("sailor_user") or {}
    email = sailor.get("email")
    if not email:
        return None
    return User.objects.filter(email__iexact=email, is_active=True).first()


@require_GET
def manifest(request):
    return JsonResponse({
        "name": "Sailor",
        "short_name": "Sailor",
        "start_url": "/pipeline/",
        "scope": "/",
        "display": "standalone",
        "background_color": "#f4f6fb",
        "theme_color": "#29307C",
        "icons": [
            {
                "src": "/static/images/favicon.png",
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "any maskable",
            }
        ],
    })


@require_GET
def service_worker(request):
    content = """
self.addEventListener('install', event => {
  self.skipWaiting();
});

self.addEventListener('activate', event => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener('push', event => {
  let payload = {};
  if (event.data) {
    try { payload = event.data.json(); } catch (err) { payload = {}; }
  }

  const title = payload.title || 'Sailor';
  const options = {
    body: payload.body || 'Open Sailor',
    icon: '/static/images/favicon.png',
    badge: '/static/images/favicon.png',
    data: { url: payload.url || '/pipeline/' },
    requireInteraction: true
  };

  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  const url = event.notification.data && event.notification.data.url
    ? event.notification.data.url
    : '/pipeline/';

  event.waitUntil((async () => {
    const allClients = await clients.matchAll({ type: 'window', includeUncontrolled: true });
    for (const client of allClients) {
      if ('focus' in client) {
        client.navigate(url);
        return client.focus();
      }
    }
    return clients.openWindow(url);
  })());
});
"""
    return HttpResponse(content, content_type="application/javascript")


@require_GET
@ensure_csrf_cookie
def setup_calling(request):
    user = _current_user(request)
    devices = []
    if user:
        devices = user.devices.filter(is_active=True).order_by("-last_seen_at", "-created_at")
    return render(request, "pwa/call_setup.html", {
        "current_user": user,
        "devices": devices,
        "vapid_public_key": settings.WEBPUSH_VAPID_PUBLIC_KEY,
    })


@require_POST
def register_device(request):
    user = _current_user(request)
    if not user:
        return JsonResponse({"ok": False, "message": "Please log in again."}, status=401)
    if not settings.WEBPUSH_VAPID_PUBLIC_KEY:
        return JsonResponse({
            "ok": False,
            "message": "Web Push keys are not configured on the server.",
        }, status=400)

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (TypeError, ValueError):
        return JsonResponse({"ok": False, "message": "Invalid device payload."}, status=400)

    subscription = payload.get("subscription") or {}
    keys = subscription.get("keys") or {}
    endpoint = subscription.get("endpoint")
    if not endpoint or not keys.get("p256dh") or not keys.get("auth"):
        return JsonResponse({"ok": False, "message": "Incomplete push subscription."}, status=400)

    device, _ = UserDevice.objects.update_or_create(
        endpoint=endpoint,
        defaults={
            "user": user,
            "name": payload.get("name", "")[:120],
            "p256dh": keys["p256dh"],
            "auth": keys["auth"],
            "user_agent": request.META.get("HTTP_USER_AGENT", ""),
            "is_active": True,
            "last_seen_at": timezone.now(),
        },
    )
    return JsonResponse({"ok": True, "message": f"{device.name or 'This phone'} is ready."})


@require_POST
def send_call_to_phone(request, lead_id):
    user = _current_user(request)
    if not user:
        return _call_response(False, "Please log in again.")

    if not settings.WEBPUSH_VAPID_PRIVATE_KEY or not settings.WEBPUSH_VAPID_PUBLIC_KEY:
        return _call_response(False, "Web Push is not configured. Add VAPID keys in the server environment.")

    lead = get_object_or_404(Lead, pk=lead_id, deleted_at__isnull=True)
    phone = (lead.phone or lead.corporate_phone or "").strip()
    if not phone:
        return _call_response(False, "This lead has no phone number.")

    devices = list(user.devices.filter(is_active=True))
    if not devices:
        return _call_response(False, "No phone is registered yet. Open Sailor on your phone and run Call Setup.")

    url = f"/call/launch/?phone={quote(phone)}&name={quote(lead.full_name)}"
    payload = json.dumps({
        "title": f"Call {lead.full_name}",
        "body": f"Tap to call {phone}",
        "url": url,
    })

    sent = 0
    for device in devices:
        try:
            from pywebpush import WebPushException, webpush

            webpush(
                subscription_info={
                    "endpoint": device.endpoint,
                    "keys": {"p256dh": device.p256dh, "auth": device.auth},
                },
                data=payload,
                vapid_private_key=settings.WEBPUSH_VAPID_PRIVATE_KEY,
                vapid_claims={"sub": settings.WEBPUSH_VAPID_SUBJECT},
            )
            sent += 1
            device.last_seen_at = timezone.now()
            device.save(update_fields=["last_seen_at", "updated_at"])
        except Exception as exc:
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            if status_code in (404, 410):
                UserDevice.objects.filter(pk=device.pk).update(is_active=False)
            logger.warning("Call push failed for device %s: %s", device.pk, exc)

    if sent:
        call_type = ActionType.objects.filter(category=ActionType.Category.PHONE).order_by("name").first()
        if call_type:
            Action.objects.create(
                lead=lead,
                action_type=call_type,
                performed_by=user,
                performed_at=timezone.now(),
                outcome="Sent to phone dialer",
                metadata={
                    "note": f"Call sent to phone for {phone}",
                    "call_status": "sent_to_phone",
                    "call_phone": phone,
                    "device_count": sent,
                },
            )
        return _call_response(True, f"Call sent to {sent} registered phone device{'' if sent == 1 else 's'}.")
    return _call_response(False, "Could not send the call notification. Re-open Call Setup on your phone.")


@require_GET
def launch_call(request):
    phone = request.GET.get("phone", "").strip()
    name = request.GET.get("name", "Lead").strip() or "Lead"
    return render(request, "pwa/call_launch.html", {
        "phone": phone,
        "name": name,
    })


def _call_response(success: bool, message: str) -> HttpResponse:
    color = "green" if success else "red"
    return HttpResponse(
        f'<div id="call-result" class="mt-2 text-xs text-{color}-700 bg-{color}-50 '
        f'border border-{color}-100 rounded-lg px-3 py-2">{message}</div>'
    )
