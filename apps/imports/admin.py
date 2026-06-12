from django.contrib import admin
from apps.imports.models import ImportBatch, ColumnMappingTemplate, LeadFieldConfig


@admin.register(LeadFieldConfig)
class LeadFieldConfigAdmin(admin.ModelAdmin):
    list_display  = ["display_label", "field_key", "sort_order", "is_active", "detect_keywords"]
    list_editable = ["sort_order", "is_active"]
    search_fields = ["field_key", "display_label"]
    ordering      = ["sort_order", "field_key"]
    fieldsets = [
        (None, {"fields": ["field_key", "display_label", "sort_order", "is_active"]}),
        ("Auto-detection", {
            "description": "JSON array of lowercase keywords that trigger auto-mapping. e.g. [\"email\",\"e-mail\"]",
            "fields": ["detect_keywords"],
        }),
    ]


@admin.register(ImportBatch)
class ImportBatchAdmin(admin.ModelAdmin):
    list_display = ["original_filename", "source", "status", "total_rows",
                    "imported_rows", "duplicate_rows", "uploaded_by", "created_at"]
    list_filter = ["status", "source"]
    readonly_fields = ["total_rows", "imported_rows", "duplicate_rows",
                       "skipped_rows", "error_log", "created_at", "updated_at"]


@admin.register(ColumnMappingTemplate)
class ColumnMappingTemplateAdmin(admin.ModelAdmin):
    list_display = ["name", "source", "created_by", "created_at"]
    search_fields = ["name"]
