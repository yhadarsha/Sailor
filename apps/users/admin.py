from django.contrib import admin
from apps.users.models import AllowedLogin, User, UserDevice, UserMailToken


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


@admin.register(UserMailToken)
class UserMailTokenAdmin(admin.ModelAdmin):
    """
    Read-only visibility into who has a persistent mail-send token.
    The encrypted cache itself is intentionally NOT shown/editable here —
    use 'has_token' to see whether a user has connected, and 'updated_at'
    to see when it was last refreshed (a stale updated_at + send failures
    usually means the refresh token has expired and they need to re-login).
    """
    list_display = ["user", "has_token", "updated_at", "created_at"]
    search_fields = ["user__display_name", "user__email"]
    readonly_fields = ["user", "has_token", "created_at", "updated_at"]
    exclude = ["encrypted_cache"]

    @admin.display(boolean=True, description="Has token")
    def has_token(self, obj):
        return bool(obj.encrypted_cache)

    def has_add_permission(self, request):
        return False
