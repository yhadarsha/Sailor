from django.contrib import admin
from apps.campaigns.models import Campaign, CampaignStep, CampaignLead, CampaignSend


class CampaignStepInline(admin.TabularInline):
    model  = CampaignStep
    extra  = 0
    fields = ("step_number", "variant_label", "step_type", "label", "wait_days", "subject_template")


@admin.register(Campaign)
class CampaignAdmin(admin.ModelAdmin):
    list_display  = ("name", "status", "created_by", "created_at")
    list_filter   = ("status",)
    inlines       = [CampaignStepInline]


@admin.register(CampaignLead)
class CampaignLeadAdmin(admin.ModelAdmin):
    list_display = ("lead", "campaign", "status", "current_step", "enrolled_at")
    list_filter  = ("status", "campaign")


@admin.register(CampaignSend)
class CampaignSendAdmin(admin.ModelAdmin):
    list_display  = ("campaign_lead", "step", "variant_label", "status", "scheduled_for", "sent_at")
    list_filter   = ("status",)
    readonly_fields = ("campaign_lead", "step", "sent_at", "opened_at")
