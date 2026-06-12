from django.contrib import admin
from apps.automation.models import Campaign, CampaignLead, AutomationRule, WebhookLog


@admin.register(Campaign)
class CampaignAdmin(admin.ModelAdmin):
    list_display = ["name", "campaign_type", "status", "created_by", "started_at"]
    list_filter = ["campaign_type", "status"]


@admin.register(CampaignLead)
class CampaignLeadAdmin(admin.ModelAdmin):
    list_display = ["campaign", "lead", "status", "sent_at"]
    list_filter = ["status", "campaign"]


@admin.register(AutomationRule)
class AutomationRuleAdmin(admin.ModelAdmin):
    list_display = ["name", "trigger_event", "is_active", "created_by"]
    list_filter = ["is_active", "trigger_event"]


@admin.register(WebhookLog)
class WebhookLogAdmin(admin.ModelAdmin):
    list_display = ["rule", "lead", "webhook_url", "status", "response_status", "created_at"]
    list_filter = ["status"]
    readonly_fields = list(f.name for f in WebhookLog._meta.fields)
