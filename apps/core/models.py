"""
Abstract base models.
Every model in this project inherits from one of these.
Rule: never instantiate these directly.
"""

import uuid
from django.db import models
from django.utils import timezone


class UUIDModel(models.Model):
    """UUID primary key. Avoids sequential ID enumeration and works across systems."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class TimeStampedModel(models.Model):
    """Automatic created_at / updated_at on every model."""

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class BaseModel(UUIDModel, TimeStampedModel):
    """
    Standard base for all models: UUID PK + timestamps.
    Use this for lookup tables and models that don't need soft-delete.
    """

    class Meta:
        abstract = True


class SoftDeleteQuerySet(models.QuerySet):
    """Default queryset that hides soft-deleted records."""

    def active(self):
        return self.filter(deleted_at__isnull=True)

    def deleted(self):
        return self.filter(deleted_at__isnull=False)

    def soft_delete(self):
        return self.update(deleted_at=timezone.now())

    def restore(self):
        return self.update(deleted_at=None)


class SoftDeleteManager(models.Manager):
    """Default manager: only returns non-deleted records."""

    def get_queryset(self):
        return SoftDeleteQuerySet(self.model, using=self._db).active()


class AllObjectsManager(models.Manager):
    """Use Model.all_objects.filter(...) to include soft-deleted records."""

    def get_queryset(self):
        return SoftDeleteQuerySet(self.model, using=self._db)


class SoftDeleteModel(BaseModel):
    """
    Adds soft-delete to BaseModel.
    - Model.objects    → excludes deleted records (default)
    - Model.all_objects → includes deleted records
    """

    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)

    objects = SoftDeleteManager()
    all_objects = AllObjectsManager()

    class Meta:
        abstract = True

    def soft_delete(self, using=None):
        self.deleted_at = timezone.now()
        self.save(update_fields=["deleted_at", "updated_at"], using=using)

    def restore(self, using=None):
        self.deleted_at = None
        self.save(update_fields=["deleted_at", "updated_at"], using=using)

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None
