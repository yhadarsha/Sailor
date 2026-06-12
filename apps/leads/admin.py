from django.contrib import admin
from apps.leads.models import Lead, Company, LeadSource, RoutingZone, LeadDuplicate


@admin.register(LeadSource)
class LeadSourceAdmin(admin.ModelAdmin):
    list_display = ["name", "source_type", "is_active", "created_at"]
    list_filter = ["source_type", "is_active"]
    search_fields = ["name"]


@admin.register(RoutingZone)
class RoutingZoneAdmin(admin.ModelAdmin):
    list_display = ["name", "action", "priority", "is_active", "city_patterns"]
    list_filter = ["action", "is_active"]
    search_fields = ["name"]


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ["name", "industry", "city", "country", "employee_count", "created_at"]
    list_filter = ["country", "industry"]
    search_fields = ["name", "normalized_name", "city"]
    readonly_fields = ["normalized_name", "created_at", "updated_at"]


@admin.register(Lead)
class LeadAdmin(admin.ModelAdmin):
    list_display = [
        "full_name", "email", "company", "city", "current_stage",
        "assigned_to", "ai_score", "created_at",
    ]
    list_filter = ["current_stage", "assigned_to", "source", "email_bounced"]
    search_fields = ["first_name", "last_name", "email", "linkedin_id", "phone"]
    readonly_fields = [
        "current_stage", "ai_score", "raw_import_data",
        "converted_at", "dead_at", "created_at", "updated_at",
    ]
    list_select_related = ["company", "current_stage", "assigned_to"]


@admin.register(LeadDuplicate)
class LeadDuplicateAdmin(admin.ModelAdmin):
    list_display = ["lead", "duplicate_lead", "match_type", "confidence_score", "resolution"]
    list_filter = ["match_type", "resolution"]
    search_fields = ["lead__first_name", "lead__email"]
