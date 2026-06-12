from django.contrib import admin
from apps.users.models import AllowedLogin, User, UserDevice


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = [
        "display_name", "email", "mobile_phone", "auth_phone",
        "business_phone", "role", "is_active", "last_synced_at",
    ]
    list_filter = ["role", "is_active"]
    search_fields = ["display_name", "email", "mobile_phone", "auth_phone", "business_phone"]
    readonly_fields = ["id", "last_synced_at", "created_at", "updated_at"]


@admin.register(UserDevice)
class UserDeviceAdmin(admin.ModelAdmin):
    list_display = ["user", "name", "is_active", "last_seen_at", "created_at"]
    list_filter = ["is_active"]
    search_fields = ["user__display_name", "user__email", "name", "endpoint"]
    readonly_fields = ["created_at", "updated_at", "last_seen_at"]


@admin.register(AllowedLogin)
class AllowedLoginAdmin(admin.ModelAdmin):
    list_display = ["email", "display_name", "role", "is_active", "created_at"]
    list_filter = ["role", "is_active"]
    search_fields = ["email", "display_name"]
    readonly_fields = ["created_at", "updated_at"]
