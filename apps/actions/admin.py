from django.contrib import admin
from apps.actions.models import ActionType, Action


@admin.register(ActionType)
class ActionTypeAdmin(admin.ModelAdmin):
    list_display = ["name", "category", "requires_outcome", "advances_stage"]
    list_filter = ["category"]


@admin.register(Action)
class ActionAdmin(admin.ModelAdmin):
    list_display = ["lead", "action_type", "performed_by", "performed_at", "bounce_detected"]
    list_filter = ["action_type", "performed_by", "bounce_detected"]
    search_fields = ["lead__first_name", "lead__last_name", "lead__email"]
    readonly_fields = ["id", "lead", "action_type", "performed_by", "performed_at",
                       "outcome", "bounce_detected", "dispatch_date", "metadata", "created_at"]

    def has_add_permission(self, request):
        return False  # Actions are append-only via application code

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
