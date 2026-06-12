"""
Management command: python manage.py seed

Populates the database with the baseline lookup data needed to use the app:
  - Pipeline stages (in correct order)
  - Action types
  - Lead sources
  - A default routing zone for Bangalore

Safe to run multiple times — uses get_or_create everywhere.
"""

from django.core.management.base import BaseCommand
from django.db import transaction


PIPELINE_STAGES = [
    # (name, order, color, is_terminal)
    ("New",              1,  "#94a3b8", False),
    ("Contacted",        2,  "#60a5fa", False),
    ("Engaged",          3,  "#818cf8", False),
    ("Follow-up",        4,  "#fb923c", False),
    ("Qualified",        5,  "#34d399", False),
    ("Dead",             6,  "#f87171", True),
    ("Bounced/Invalid",  7,  "#fbbf24", True),
    ("Converted",        8,  "#10b981", True),
]

ACTION_TYPES = [
    # (name, category, requires_outcome, advances_stage, icon)
    ("LI Connect",   "linkedin", False, True,  "linkedin"),
    ("LI Message",   "linkedin", False, True,  "linkedin"),
    ("Email Sent",   "email",    False, True,  "mail"),
    ("Cold Call",    "phone",    True,  True,  "phone"),
    ("Post Sent",    "physical", False, False, "package"),
    ("Note",         "internal", False, False, "file-text"),
]

LEAD_SOURCES = [
    # (name, source_type)
    ("LinkedIn Searches", "manual"),
    ("Apollo",            "database"),
    ("DataLyzer",         "database"),
    ("QCFI",              "database"),
    ("Industrial Hubs",   "database"),
    ("Other Databases",   "database"),
    ("Manual Entry",      "manual"),
]

ROUTING_ZONES = [
    # (name, city_patterns, action, priority)
    (
        "Bangalore",
        ["bangalore", "bengaluru", "blr"],
        "physical_post",
        10,
    ),
]


class Command(BaseCommand):
    help = "Seed the database with baseline lookup data (stages, sources, action types)"

    def handle(self, *args, **options):
        with transaction.atomic():
            self._seed_stages()
            self._seed_action_types()
            self._seed_lead_sources()
            self._seed_routing_zones()

        self.stdout.write(self.style.SUCCESS("\n✓ Seed complete. Your pipeline is ready.\n"))

    def _seed_stages(self):
        from apps.pipeline.models import PipelineStage

        self.stdout.write("  Pipeline stages...")
        for name, order, color, is_terminal in PIPELINE_STAGES:
            obj, created = PipelineStage.objects.get_or_create(
                name=name,
                defaults={"order": order, "color": color, "is_terminal": is_terminal},
            )
            if created:
                self.stdout.write(f"    + {name}")
            else:
                # Keep order/color up to date if stage already exists
                changed = False
                if obj.order != order:
                    obj.order = order
                    changed = True
                if obj.color != color:
                    obj.color = color
                    changed = True
                if changed:
                    obj.save()
                    self.stdout.write(f"    ~ {name} (updated)")

    def _seed_action_types(self):
        from apps.actions.models import ActionType

        self.stdout.write("  Action types...")
        for name, category, requires_outcome, advances_stage, icon in ACTION_TYPES:
            obj, created = ActionType.objects.get_or_create(
                name=name,
                defaults={
                    "category":        category,
                    "requires_outcome": requires_outcome,
                    "advances_stage":   advances_stage,
                    "icon":             icon,
                },
            )
            if created:
                self.stdout.write(f"    + {name}")

    def _seed_lead_sources(self):
        from apps.leads.models import LeadSource

        self.stdout.write("  Lead sources...")
        for name, source_type in LEAD_SOURCES:
            obj, created = LeadSource.objects.get_or_create(
                name=name,
                defaults={"source_type": source_type, "is_active": True},
            )
            if created:
                self.stdout.write(f"    + {name}")

    def _seed_routing_zones(self):
        from apps.leads.models import RoutingZone

        self.stdout.write("  Routing zones...")
        for name, city_patterns, action, priority in ROUTING_ZONES:
            obj, created = RoutingZone.objects.get_or_create(
                name=name,
                defaults={
                    "city_patterns": city_patterns,
                    "action":        action,
                    "priority":      priority,
                    "is_active":     True,
                },
            )
            if created:
                self.stdout.write(f"    + {name} → {action}")
