from django.contrib import admin
from apps.pipeline.models import PipelineStage, LeadStageHistory


@admin.register(PipelineStage)
class PipelineStageAdmin(admin.ModelAdmin):
    list_display = ["name", "order", "color", "is_terminal"]
    list_editable = ["order"]
    ordering = ["order"]


@admin.register(LeadStageHistory)
class LeadStageHistoryAdmin(admin.ModelAdmin):
    list_display = ["lead", "from_stage", "to_stage", "changed_by", "auto_changed", "changed_at"]
    list_filter = ["to_stage", "auto_changed", "changed_by"]
    search_fields = ["lead__first_name", "lead__last_name", "lead__email"]
    readonly_fields = ["id", "lead", "from_stage", "to_stage", "changed_by", "changed_at", "reason", "auto_changed"]

    def has_add_permission(self, request):
        return False  # History is append-only via application code

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
