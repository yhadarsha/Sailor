"""
Users app.

Stores a local cache of Azure AD users.
This app owns NO authentication logic — that is entirely handled by Azure AD SSO.
The id field IS the AAD Object ID (UUID), so there is no mismatch between JWT claims and DB.
"""

import uuid
from django.db import models
from apps.core.models import TimeStampedModel


class User(TimeStampedModel):
    """
    Azure AD user, cached locally.

    Primary key = AAD Object ID (set explicitly from the JWT 'oid' claim on first login).
    No password field. No Django auth backend needed here.
    """

    class Role(models.TextChoices):
        ADMIN = "admin", "Admin"
        SALES = "sales", "Sales Rep"
        VIEWER = "viewer", "Viewer"

    # AAD Object ID is the PK.
    # default=uuid.uuid4 allows manual creation via admin (pre-AAD integration).
    # When AAD SSO is wired up, the JWT 'oid' claim is passed explicitly on get_or_create,
    # overriding this default so the PK always matches the AAD Object ID.
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True, db_index=True)
    display_name = models.CharField(max_length=255)
    mobile_phone = models.CharField(max_length=40, blank=True)
    business_phone = models.CharField(max_length=40, blank=True)
    auth_phone = models.CharField(
        max_length=40,
        blank=True,
        help_text="Phone number read from Microsoft authentication methods when permitted.",
    )
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.SALES)
    is_active = models.BooleanField(default=True, db_index=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "users"
        verbose_name = "User"
        verbose_name_plural = "Users"
        ordering = ["display_name"]

    def __str__(self) -> str:
        return f"{self.display_name} <{self.email}>"

    @property
    def call_handoff_phone(self) -> str:
        return self.mobile_phone or self.auth_phone or self.business_phone


class UserDevice(TimeStampedModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="devices")
    name = models.CharField(max_length=120, blank=True)
    endpoint = models.TextField(unique=True)
    p256dh = models.TextField()
    auth = models.TextField()
    user_agent = models.TextField(blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "user_devices"
        ordering = ["-last_seen_at", "-created_at"]

    def __str__(self) -> str:
        label = self.name or "PWA device"
        return f"{label} for {self.user.display_name}"


class AllowedLogin(TimeStampedModel):
    """
    Admin-managed SSO allow-list.

    Microsoft still authenticates the person, but Sailor only creates a session
    when the signed-in email is active in this table.
    """

    email = models.EmailField(unique=True, db_index=True)
    display_name = models.CharField(max_length=255, blank=True)
    role = models.CharField(max_length=20, choices=User.Role.choices, default=User.Role.SALES)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "allowed_logins"
        verbose_name = "Allowed login"
        verbose_name_plural = "Allowed logins"
        ordering = ["email"]

    def save(self, *args, **kwargs):
        self.email = (self.email or "").strip().lower()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        label = self.display_name or self.email
        return f"{label} <{self.email}>"
