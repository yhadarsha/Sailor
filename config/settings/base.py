"""
Base settings — shared across all environments.
Do NOT import this directly; use development.py or production.py.
"""

from pathlib import Path
import environ

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# ── Environment ───────────────────────────────────────────────────────────────
env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env")

# ── Core ──────────────────────────────────────────────────────────────────────
SECRET_KEY = env("SECRET_KEY")
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"
LOGIN_REDIRECT_URL = "/pipeline/"

# ── Apps ──────────────────────────────────────────────────────────────────────
DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "rest_framework_simplejwt",
    "django_filters",
    "django_extensions",
    "import_export",
    "django_celery_beat",
]

LOCAL_APPS = [
    "apps.core",
    "apps.users",
    "apps.leads",
    "apps.pipeline",
    "apps.actions",
    "apps.imports",
    "apps.ai_layer",
    "apps.automation",
    "apps.campaigns",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

# ── Session ───────────────────────────────────────────────────────────────────
SESSION_COOKIE_AGE              = 28800   # 8 hours
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_COOKIE_HTTPONLY         = True
SESSION_COOKIE_SAMESITE         = "Lax"

# ── Middleware ────────────────────────────────────────────────────────────────
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Sailor SSO — must come after SessionMiddleware
    "apps.core.middleware.SailorAuthMiddleware",
]

# ── Templates ─────────────────────────────────────────────────────────────────
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                # Injects `sailor_user` dict into every template
                "apps.core.context_processors.sailor_auth_user",
            ],
        },
    },
]

# ── Database ──────────────────────────────────────────────────────────────────
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("DB_NAME"),
        "USER": env("DB_USER"),
        "PASSWORD": env("DB_PASSWORD"),
        "HOST": env("DB_HOST", default="localhost"),
        "PORT": env("DB_PORT", default="5432"),
        "OPTIONS": {
            "options": "-c timezone=UTC",
        },
        "CONN_MAX_AGE": 60,  # Persistent connections
    }
}

# ── Internationalisation ──────────────────────────────────────────────────────
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True  # Always store UTC in DB, display in IST

# ── Static Files ──────────────────────────────────────────────────────────────
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# ── REST Framework ────────────────────────────────────────────────────────────
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
    "DEFAULT_FILTER_BACKENDS": (
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ),
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
}

# ── Celery ────────────────────────────────────────────────────────────────────
CELERY_BROKER_URL     = env("REDIS_URL", default="redis://localhost:6379/0")
CELERY_RESULT_BACKEND = env("REDIS_URL", default="redis://localhost:6379/0")
CELERY_TIMEZONE       = TIME_ZONE
CELERY_TASK_TRACK_STARTED = True
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"

# Periodic tasks — process due campaign sends every 5 minutes
from celery.schedules import crontab
CELERY_BEAT_SCHEDULE = {
    "process-due-campaign-sends": {
        "task":     "apps.campaigns.tasks.process_due_campaign_sends",
        "schedule": 300,  # every 5 minutes
    },
}

# ── Azure AD SSO ──────────────────────────────────────────────────────────────
AZURE_AD_TENANT_ID     = env("AZURE_AD_TENANT_ID",     default="")
AZURE_AD_CLIENT_ID     = env("AZURE_AD_CLIENT_ID",     default="")
AZURE_AD_CLIENT_SECRET = env("AZURE_AD_CLIENT_SECRET", default="")
AZURE_AD_REDIRECT_URI  = env("AZURE_AD_REDIRECT_URI",  default="http://localhost:8000/auth/callback/")
AZURE_AD_AUTHORITY     = f"https://login.microsoftonline.com/{AZURE_AD_TENANT_ID}"
AZURE_AD_SCOPES        = env.list(
    "AZURE_AD_SCOPES",
    default=["User.Read", "Mail.Send"],
)

# Reply-To routing: set to your domain (e.g. "datalyzerint.com") to enable reply tracking.
# Sailor will set Reply-To: sailor+<action_id>@<SAILOR_REPLY_DOMAIN> on outgoing emails.
# Configure a catch-all/forward rule on that mailbox to POST to /email-reply/<action_id>/
SAILOR_REPLY_DOMAIN = env("SAILOR_REPLY_DOMAIN", default="")

# Web Push for PWA call handoff. Generate stable VAPID keys before using push:
#   python -m py_vapid --gen
WEBPUSH_VAPID_PUBLIC_KEY  = env("WEBPUSH_VAPID_PUBLIC_KEY", default="")
WEBPUSH_VAPID_PRIVATE_KEY = env("WEBPUSH_VAPID_PRIVATE_KEY", default="")
WEBPUSH_VAPID_SUBJECT     = env("WEBPUSH_VAPID_SUBJECT", default="mailto:admin@sailor.local")

# ── AI ────────────────────────────────────────────────────────────────────────
OPENAI_API_KEY = env("OPENAI_API_KEY", default="")
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMENSIONS = 1536
