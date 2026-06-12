"""
Management command: python manage.py seed_test_data

Creates realistic test data so the pipeline board is fully populated:
  - 2 sales users  (Shirish, Shreya)
  - 10 companies
  - 30 leads spread across all pipeline stages
  - Actions logged on several leads

Run AFTER: python manage.py seed  (which creates stages, sources, action types)
Safe to run multiple times — clears test leads first to avoid duplicates.
"""

import random
import uuid
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db import transaction


# ── Test data pools ───────────────────────────────────────────────────────────

USERS = [
    {"display_name": "Shirish Kumar", "email": "shirish@company.com", "role": "sales"},
    {"display_name": "Shreya Nair",   "email": "shreya@company.com",  "role": "sales"},
]

COMPANIES = [
    ("Tata Consultancy Services", "IT Services",      "Mumbai",    "Maharashtra"),
    ("Infosys",                   "IT Services",      "Bangalore", "Karnataka"),
    ("Wipro",                     "IT Services",      "Bangalore", "Karnataka"),
    ("L&T Construction",          "Construction",     "Chennai",   "Tamil Nadu"),
    ("Bosch India",               "Manufacturing",    "Bangalore", "Karnataka"),
    ("ABB India",                 "Manufacturing",    "Bangalore", "Karnataka"),
    ("Siemens India",             "Engineering",      "Mumbai",    "Maharashtra"),
    ("BHEL",                      "Engineering",      "Hyderabad", "Telangana"),
    ("Mahindra CIE",              "Auto Components",  "Pune",      "Maharashtra"),
    ("Godrej Industries",         "Conglomerate",     "Mumbai",    "Maharashtra"),
]

LEADS = [
    # (first, last, title, company_idx, city, source_name, stage_name)
    ("Ravi",      "Shankar",    "VP Engineering",         0, "Mumbai",    "Apollo",            "New"),
    ("Priya",     "Mehta",      "Head of Operations",     1, "Bangalore", "LinkedIn Searches", "New"),
    ("Arjun",     "Verma",      "Plant Manager",          3, "Chennai",   "QCFI",              "New"),
    ("Sneha",     "Reddy",      "Purchase Manager",       4, "Bangalore", "DataLyzer",         "New"),
    ("Kiran",     "Joshi",      "GM Manufacturing",       8, "Pune",      "Apollo",            "New"),
    ("Vikram",    "Singh",      "CTO",                    2, "Bangalore", "LinkedIn Searches", "Contacted"),
    ("Ananya",    "Iyer",       "Director Technology",    5, "Bangalore", "Apollo",            "Contacted"),
    ("Rahul",     "Gupta",      "Head of Procurement",    6, "Mumbai",    "Industrial Hubs",   "Contacted"),
    ("Deepa",     "Nair",       "Operations Head",        9, "Mumbai",    "DataLyzer",         "Contacted"),
    ("Suresh",    "Pillai",     "Senior Manager",         7, "Hyderabad", "QCFI",              "Contacted"),
    ("Meera",     "Krishnan",   "VP Operations",          1, "Bangalore", "Apollo",            "Engaged"),
    ("Aditya",    "Sharma",     "Head of IT",             0, "Mumbai",    "LinkedIn Searches", "Engaged"),
    ("Pooja",     "Desai",      "Plant Head",             3, "Chennai",   "Industrial Hubs",   "Engaged"),
    ("Nikhil",    "Patel",      "GM Purchase",            8, "Pune",      "DataLyzer",         "Engaged"),
    ("Lakshmi",   "Venkat",     "DGM Engineering",        5, "Bangalore", "QCFI",              "Engaged"),
    ("Sanjay",    "Kulkarni",   "COO",                    6, "Mumbai",    "Apollo",            "Follow-up"),
    ("Divya",     "Rao",        "Procurement Head",       2, "Bangalore", "LinkedIn Searches", "Follow-up"),
    ("Amit",      "Malhotra",   "VP Manufacturing",       7, "Hyderabad", "Industrial Hubs",   "Follow-up"),
    ("Kavya",     "Menon",      "Head of Projects",       4, "Bangalore", "DataLyzer",         "Follow-up"),
    ("Rajesh",    "Nambiar",    "Director Operations",    9, "Mumbai",    "QCFI",              "Follow-up"),
    ("Harish",    "Babu",       "MD",                     5, "Bangalore", "Apollo",            "Qualified"),
    ("Sunita",    "Agarwal",    "CEO",                    0, "Mumbai",    "LinkedIn Searches", "Qualified"),
    ("Prasad",    "Reddy",      "Executive Director",     3, "Chennai",   "Industrial Hubs",   "Qualified"),
    ("Anjali",    "Shah",       "VP Procurement",         8, "Pune",      "DataLyzer",         "Dead"),
    ("Manoj",     "Tiwari",     "Senior Engineer",        6, "Mumbai",    "QCFI",              "Dead"),
    ("Geeta",     "Pillai",     "Plant Supervisor",       7, "Hyderabad", "Apollo",            "Bounced/Invalid"),
    ("Sunil",     "Jain",       "Manager",                2, "Bangalore", "Industrial Hubs",   "Bounced/Invalid"),
    ("Nisha",     "Kapoor",     "Director",               1, "Bangalore", "LinkedIn Searches", "Converted"),
    ("Rohit",     "Saxena",     "GM Operations",          4, "Bangalore", "Apollo",            "Converted"),
    ("Padma",     "Narayanan",  "Head of Strategy",       9, "Mumbai",    "DataLyzer",         "Converted"),
]

ACTIONS_SEED = [
    # (lead_index, action_type_name, days_ago, outcome)
    (5,  "LI Connect",  10, ""),
    (5,  "LI Message",   8, ""),
    (6,  "LI Connect",   9, ""),
    (6,  "Email Sent",   7, ""),
    (7,  "LI Connect",  12, ""),
    (7,  "Cold Call",    6, "Not reachable, try again"),
    (10, "LI Connect",  15, ""),
    (10, "LI Message",  12, ""),
    (10, "Email Sent",   8, "Opened — no reply"),
    (11, "LI Connect",  14, ""),
    (11, "Email Sent",  10, ""),
    (11, "Cold Call",    5, "Interested, asked for brochure"),
    (15, "LI Connect",  18, ""),
    (15, "LI Message",  15, ""),
    (15, "Email Sent",  12, ""),
    (15, "Cold Call",    7, "Meeting scheduled for next week"),
    (20, "LI Connect",  20, ""),
    (20, "LI Message",  17, ""),
    (20, "Email Sent",  14, ""),
    (20, "Cold Call",    9, "Very interested — needs pricing"),
    (20, "Post Sent",    5, ""),
    (21, "LI Connect",  22, ""),
    (21, "Email Sent",  18, ""),
    (21, "Cold Call",   10, "Demo completed — decision pending"),
    (21, "Post Sent",    6, ""),
    (27, "LI Connect",  25, ""),
    (27, "LI Message",  20, ""),
    (27, "Email Sent",  15, ""),
    (27, "Cold Call",    8, "Closed — signed contract"),
    (28, "LI Connect",  22, ""),
    (28, "Email Sent",  16, ""),
    (28, "Cold Call",    7, "Won — PO received"),
]


class Command(BaseCommand):
    help = "Create test data: users, companies, leads, and actions for pipeline testing"

    def add_arguments(self, parser):
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Clear existing test leads before seeding (keeps stages/sources/users)",
        )

    def handle(self, *args, **options):
        from apps.pipeline.models import PipelineStage
        from apps.leads.models import LeadSource

        # Guard: make sure seed has been run first
        if not PipelineStage.objects.exists():
            self.stdout.write(self.style.ERROR(
                "No pipeline stages found. Run `python manage.py seed` first."
            ))
            return

        with transaction.atomic():
            if options["clear"]:
                self._clear_test_data()

            users    = self._seed_users()
            companies = self._seed_companies()
            leads    = self._seed_leads(users, companies)
            self._seed_actions(leads, users)

        self.stdout.write(self.style.SUCCESS(
            f"\n✓ Test data ready — {len(leads)} leads across all stages.\n"
            f"  Open http://127.0.0.1:8000/pipeline/ to see the board.\n"
        ))

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _clear_test_data(self):
        from apps.leads.models import Lead
        deleted, _ = Lead.all_objects.filter(
            email__endswith="@testlms.local"
        ).delete()
        self.stdout.write(f"  Cleared {deleted} existing test leads.")

    def _seed_users(self):
        from apps.users.models import User

        self.stdout.write("  Users...")
        result = []
        for u in USERS:
            obj, created = User.objects.get_or_create(
                email=u["email"],
                defaults={
                    "id":           uuid.uuid4(),
                    "display_name": u["display_name"],
                    "role":         u["role"],
                    "is_active":    True,
                },
            )
            result.append(obj)
            if created:
                self.stdout.write(f"    + {obj.display_name}")
        return result

    def _seed_companies(self):
        from apps.leads.models import Company

        self.stdout.write("  Companies...")
        result = []
        for name, industry, city, state in COMPANIES:
            obj, created = Company.objects.get_or_create(
                normalized_name=name.lower().strip(),
                city=city,
                defaults={
                    "name":     name,
                    "industry": industry,
                    "state":    state,
                    "country":  "India",
                },
            )
            result.append(obj)
            if created:
                self.stdout.write(f"    + {name}")
        return result

    def _seed_leads(self, users, companies):
        from apps.leads.models import Lead, LeadSource
        from apps.pipeline.models import PipelineStage, LeadStageHistory

        self.stdout.write("  Leads...")

        stage_map  = {s.name: s for s in PipelineStage.objects.all()}
        source_map = {s.name: s for s in LeadSource.objects.all()}

        created_leads = []
        for i, (first, last, title, co_idx, city, source_name, stage_name) in enumerate(LEADS):
            email = f"{first.lower()}.{last.lower()}@testlms.local"

            stage  = stage_map.get(stage_name)
            source = source_map.get(source_name)
            owner  = users[i % len(users)]  # Alternate between Shirish and Shreya

            lead, created = Lead.all_objects.get_or_create(
                email=email,
                defaults={
                    "first_name":  first,
                    "last_name":   last,
                    "title":       title,
                    "company":     companies[co_idx],
                    "city":        city,
                    "country":     "India",
                    "source":      source,
                    "assigned_to": owner,
                    "phone":       f"+91 9{random.randint(100000000, 999999999)}",
                },
            )

            if created:
                # Write initial stage history (signal will set current_stage)
                if stage:
                    LeadStageHistory.objects.create(
                        lead=lead,
                        from_stage=None,
                        to_stage=stage,
                        changed_by=owner,
                        reason="Initial stage — seeded",
                        auto_changed=False,
                    )
                created_leads.append(lead)
                self.stdout.write(f"    + {first} {last} → {stage_name}")

        return created_leads

    def _seed_actions(self, leads, users):
        from apps.actions.models import Action, ActionType

        if not leads:
            return

        self.stdout.write("  Actions...")
        type_map = {a.name: a for a in ActionType.objects.all()}
        now = timezone.now()
        count = 0

        for lead_idx, action_name, days_ago, outcome in ACTIONS_SEED:
            if lead_idx >= len(leads):
                continue
            action_type = type_map.get(action_name)
            if not action_type:
                continue

            lead = leads[lead_idx]
            performed_at = now - timedelta(days=days_ago)

            Action.objects.create(
                lead=lead,
                action_type=action_type,
                performed_by=users[lead_idx % len(users)],
                performed_at=performed_at,
                outcome=outcome,
            )
            count += 1

        self.stdout.write(f"    + {count} action log entries")
