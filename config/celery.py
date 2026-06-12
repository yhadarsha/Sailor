"""
Celery application for Sailor LMS.

Start worker:   celery -A config worker -l info
Start beat:     celery -A config beat -l info --scheduler django_celery_beat.schedulers:DatabaseScheduler

Or combined (dev only):
    celery -A config worker --beat -l info
"""

import os
from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

app = Celery("sailor")

# Read config from Django settings, namespace CELERY_
app.config_from_object("django.conf:settings", namespace="CELERY")

# Auto-discover tasks in all INSTALLED_APPS
app.autodiscover_tasks()
