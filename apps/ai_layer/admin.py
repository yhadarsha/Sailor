from django.contrib import admin
from apps.ai_layer.models import AILeadScore, AIInsight


@admin.register(AILeadScore)
class AILeadScoreAdmin(admin.ModelAdmin):
    list_display = ["lead", "score", "model_version", "created_at"]
    list_filter = ["model_version"]
    readonly_fields = ["id", "lead", "score", "model_version", "factors", "created_at", "updated_at"]


@admin.register(AIInsight)
class AIInsightAdmin(admin.ModelAdmin):
    list_display = ["lead", "insight_type", "model_version", "feedback", "created_at"]
    list_filter = ["insight_type", "feedback", "model_version"]
    readonly_fields = ["id", "lead", "insight_type", "content", "model_version", "created_at"]
