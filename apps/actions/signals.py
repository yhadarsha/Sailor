"""
Actions signals.

Handles side-effects of logging actions:
- Bounce detection: when an email action with bounce_detected=True is saved,
  automatically flag the parent Lead.email_bounced = True.
"""

from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.actions.models import Action


@receiver(post_save, sender=Action)
def handle_bounce_detection(sender, instance: Action, created: bool, **kwargs):
    """
    If an email action with bounce_detected=True is logged,
    mark the lead's email as bounced immediately.
    """
    if not created:
        return

    if instance.bounce_detected and instance.action_type.category == "email":
        type(instance.lead).all_objects.filter(pk=instance.lead_id).update(
            email_bounced=True
        )
