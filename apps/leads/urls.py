"""
leads URLs — read-only JSON API routes.
Mounted under /api/v1/ in config/urls.py.
"""

from django.urls import path

from apps.leads import api

app_name = "leads"

urlpatterns = [
    path("leads/", api.leads_list, name="leads_list"),
    path("leads/<uuid:lead_id>/", api.lead_detail, name="lead_detail"),
]
