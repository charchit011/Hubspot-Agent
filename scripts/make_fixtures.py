#!/usr/bin/env python3
"""Generate synthetic HubSpot schema fixtures.

Exists because the portal token is not available yet, and scripts 02-06 need
something real-shaped to run against. The fixtures deliberately include the
awkward cases — a calculated property, an over-length name, an oversized
picklist, a low-fill property, a custom object with a pipeline — so the review
and validation layers are exercised rather than merely executed.

    python scripts/make_fixtures.py
    python scripts/01_fetch_hubspot.py --fixture
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures"

random.seed(20260817)  # deterministic — fixtures must not churn between runs


def prop(name, label, type_, field_type, **extra):
    p = {
        "name": name,
        "label": label,
        "type": type_,
        "fieldType": field_type,
        "groupName": extra.pop("group", "contactinformation"),
        "calculated": extra.pop("calculated", False),
        "hidden": False,
        "description": extra.pop("description", ""),
    }
    p.update(extra)
    return p


def options(*pairs):
    return [{"label": lbl, "value": val, "displayOrder": i, "hidden": False}
            for i, (lbl, val) in enumerate(pairs)]


CONTACTS = [
    prop("firstname", "First Name", "string", "text"),
    prop("lastname", "Last Name", "string", "text"),
    prop("email", "Email", "string", "text"),
    prop("phone", "Phone Number", "string", "phonenumber"),
    prop("mobilephone", "Mobile Phone", "string", "phonenumber"),
    prop("jobtitle", "Job Title", "string", "text"),
    prop("company", "Company Name", "string", "text"),
    prop("website", "Website URL", "string", "text"),
    prop("city", "City", "string", "text"),
    prop("state", "State/Region", "string", "text"),
    prop("zip", "Postal Code", "string", "text"),
    prop("country", "Country", "string", "text"),
    prop("hs_object_id", "Record ID", "number", "number"),
    prop("hubspot_owner_id", "Contact owner", "enumeration", "select"),
    prop("lifecyclestage", "Lifecycle Stage", "enumeration", "select",
         options=options(("Subscriber", "subscriber"), ("Lead", "lead"),
                         ("MQL", "marketingqualifiedlead"),
                         ("SQL", "salesqualifiedlead"),
                         ("Customer", "customer"))),
    prop("notes_last_updated", "Last Activity Date", "datetime", "date"),
    prop("engagement_score", "Engagement Score", "number", "calculation_equation",
         calculated=True,
         calculationFormula="sum(page_views * 2, form_submissions * 10)",
         description="Weighted engagement. Does NOT translate to an SF formula."),
    prop("preferred_contact_methods", "Preferred Contact Methods",
         "enumeration", "checkbox",
         options=options(("Email", "email"), ("Phone", "phone"),
                         ("SMS", "sms"), ("Post", "post"))),
    prop("internal_bio_and_background_notes_from_sales_team",
         "Internal Bio and Background Notes From Sales Team",
         "string", "textarea",
         description="Name exceeds 40 chars once suffixed — needs a short name."),
    prop("legacy_crm_identifier", "Legacy CRM Identifier", "string", "text",
         description="Filled on ~3% of records. Probably not worth migrating."),
    prop("gdpr_consent", "GDPR Consent", "bool", "booleancheckbox"),
    prop("hs_lead_status", "Lead Status", "enumeration", "select",
         options=options(("New", "NEW"), ("Open", "OPEN"),
                         ("In Progress", "IN_PROGRESS"), ("Unqualified", "UNQUALIFIED"))),
]

COMPANIES = [
    prop("name", "Company name", "string", "text"),
    prop("domain", "Company Domain Name", "string", "text"),
    prop("phone", "Phone Number", "string", "phonenumber"),
    prop("industry", "Industry", "enumeration", "select",
         options=options(*[(f"Industry {i}", f"IND_{i:03d}") for i in range(1, 121)])),
    prop("numberofemployees", "Number of Employees", "number", "number"),
    prop("annualrevenue", "Annual Revenue", "number", "number"),
    prop("description", "Description", "string", "textarea"),
    prop("city", "City", "string", "text"),
    prop("country", "Country", "string", "text"),
    prop("hs_object_id", "Record ID", "number", "number"),
    prop("hubspot_owner_id", "Company owner", "enumeration", "select"),
    prop("account_tier", "Account Tier", "enumeration", "select",
         options=options(("Platinum", "platinum"), ("Gold", "gold"),
                         ("Silver", "silver"), ("Bronze", "bronze"))),
]

DEALS = [
    prop("dealname", "Deal Name", "string", "text"),
    prop("amount", "Amount", "number", "number"),
    prop("closedate", "Close Date", "datetime", "date"),
    prop("dealstage", "Deal Stage", "enumeration", "select"),
    prop("pipeline", "Pipeline", "enumeration", "select"),
    prop("dealtype", "Deal Type", "enumeration", "select",
         options=options(("New Business", "newbusiness"), ("Existing", "existingbusiness"))),
    prop("hs_object_id", "Record ID", "number", "number"),
    prop("hubspot_owner_id", "Deal owner", "enumeration", "select"),
    prop("hs_forecast_amount", "Forecast Amount", "number", "calculation_equation",
         calculated=True, calculationFormula="amount * hs_deal_stage_probability"),
    prop("competitor_notes", "Competitor Notes", "string", "textarea"),
]

TICKETS = [
    prop("subject", "Ticket Name", "string", "text"),
    prop("content", "Ticket Description", "string", "textarea"),
    prop("hs_pipeline", "Pipeline", "enumeration", "select"),
    prop("hs_pipeline_stage", "Ticket Status", "enumeration", "select"),
    prop("hs_ticket_priority", "Priority", "enumeration", "select",
         options=options(("Low", "LOW"), ("Medium", "MEDIUM"), ("High", "HIGH"))),
    prop("hs_object_id", "Record ID", "number", "number"),
    prop("hubspot_owner_id", "Ticket owner", "enumeration", "select"),
]

SERVICE_ORDERS = [
    prop("order_number", "Order Number", "string", "text", group="serviceorder"),
    prop("order_status", "Order Status", "enumeration", "select", group="serviceorder",
         options=options(("Draft", "draft"), ("Scheduled", "scheduled"),
                         ("In Progress", "in_progress"), ("Complete", "complete"))),
    prop("scheduled_date", "Scheduled Date", "date", "date", group="serviceorder"),
    prop("order_value", "Order Value", "number", "number", group="serviceorder"),
    prop("technician_notes", "Technician Notes", "string", "textarea", group="serviceorder"),
    prop("requires_followup", "Requires Follow-up", "bool", "booleancheckbox",
         group="serviceorder"),
    prop("hs_object_id", "Record ID", "number", "number", group="serviceorder"),
    prop("hubspot_owner_id", "Order owner", "enumeration", "select", group="serviceorder"),
]

SCHEMAS = [
    {"name": "contacts", "objectTypeId": "0-1",
     "labels": {"singular": "Contact", "plural": "Contacts"},
     "primaryDisplayProperty": "email"},
    {"name": "companies", "objectTypeId": "0-2",
     "labels": {"singular": "Company", "plural": "Companies"},
     "primaryDisplayProperty": "name"},
    {"name": "deals", "objectTypeId": "0-3",
     "labels": {"singular": "Deal", "plural": "Deals"},
     "primaryDisplayProperty": "dealname"},
    {"name": "tickets", "objectTypeId": "0-5",
     "labels": {"singular": "Ticket", "plural": "Tickets"},
     "primaryDisplayProperty": "subject"},
    {"name": "service_orders", "objectTypeId": "2-1140301",
     "labels": {"singular": "Service Order", "plural": "Service Orders"},
     "primaryDisplayProperty": "order_number",
     "requiredProperties": ["order_number"]},
]

PIPELINES = {
    "deals": [
        {"id": "default", "label": "Sales Pipeline", "displayOrder": 0, "stages": [
            {"id": "appointmentscheduled", "label": "Appointment Scheduled",
             "displayOrder": 0, "metadata": {"probability": "0.2"}},
            {"id": "qualifiedtobuy", "label": "Qualified To Buy",
             "displayOrder": 1, "metadata": {"probability": "0.4"}},
            {"id": "presentationscheduled", "label": "Presentation Scheduled",
             "displayOrder": 2, "metadata": {"probability": "0.6"}},
            {"id": "closedwon", "label": "Closed Won",
             "displayOrder": 3, "metadata": {"probability": "1.0", "isClosed": "true"}},
            {"id": "closedlost", "label": "Closed Lost",
             "displayOrder": 4, "metadata": {"probability": "0.0", "isClosed": "true"}},
        ]},
        {"id": "renewals", "label": "Renewals Pipeline", "displayOrder": 1, "stages": [
            {"id": "renewal_due", "label": "Renewal Due", "displayOrder": 0,
             "metadata": {"probability": "0.5"}},
            {"id": "renewal_won", "label": "Renewal Won", "displayOrder": 1,
             "metadata": {"probability": "1.0", "isClosed": "true"}},
        ]},
    ],
    "tickets": [
        {"id": "support", "label": "Support Pipeline", "displayOrder": 0, "stages": [
            {"id": "new", "label": "New", "displayOrder": 0},
            {"id": "waiting", "label": "Waiting on Contact", "displayOrder": 1},
            {"id": "closed", "label": "Closed", "displayOrder": 2},
        ]},
    ],
    "service_orders": [
        {"id": "fulfilment", "label": "Fulfilment Pipeline", "displayOrder": 0, "stages": [
            {"id": "draft", "label": "Draft", "displayOrder": 0},
            {"id": "scheduled", "label": "Scheduled", "displayOrder": 1},
            {"id": "complete", "label": "Complete", "displayOrder": 2},
        ]},
    ],
}

OWNERS = [
    {"id": "101", "email": "amara.osei@example.com", "firstName": "Amara",
     "lastName": "Osei", "archived": False, "userId": 5001},
    {"id": "102", "email": "j.lindqvist@example.com", "firstName": "Jonas",
     "lastName": "Lindqvist", "archived": False, "userId": 5002},
    {"id": "103", "email": "priya.raman@example.com", "firstName": "Priya",
     "lastName": "Raman", "archived": False, "userId": 5003},
    {"id": "104", "email": "former.staff@example.com", "firstName": "Former",
     "lastName": "Staff", "archived": True, "userId": 5004},
    {"id": "105", "email": "", "firstName": "Shared", "lastName": "Inbox",
     "archived": False, "userId": 5005},
]

ASSOCIATIONS = {
    "contacts__to__companies": [
        {"category": "HUBSPOT_DEFINED", "typeId": 1, "label": "Primary"}],
    "deals__to__contacts": [
        {"category": "HUBSPOT_DEFINED", "typeId": 3, "label": None},
        {"category": "USER_DEFINED", "typeId": 45, "label": "Decision Maker"},
        {"category": "USER_DEFINED", "typeId": 47, "label": "Influencer"}],
    "deals__to__companies": [
        {"category": "HUBSPOT_DEFINED", "typeId": 5, "label": None}],
    "tickets__to__contacts": [
        {"category": "HUBSPOT_DEFINED", "typeId": 16, "label": None}],
    "service_orders__to__companies": [
        {"category": "USER_DEFINED", "typeId": 89, "label": "Serviced Account"}],
    "service_orders__to__deals": [
        {"category": "USER_DEFINED", "typeId": 91, "label": "Originating Deal"}],
}

# Fill rates chosen to trip the review triggers: legacy_crm_identifier is
# nearly empty, and several fields are short of 100% so "required" is unsafe.
FILL_RATES = {
    "contacts": {
        "firstname": 0.98, "lastname": 1.0, "email": 1.0, "phone": 0.71,
        "mobilephone": 0.34, "jobtitle": 0.62, "company": 0.88, "website": 0.29,
        "city": 0.55, "state": 0.41, "zip": 0.48, "country": 0.60,
        "hs_object_id": 1.0, "hubspot_owner_id": 0.94, "lifecyclestage": 1.0,
        "notes_last_updated": 0.86, "engagement_score": 0.77,
        "preferred_contact_methods": 0.22,
        "internal_bio_and_background_notes_from_sales_team": 0.13,
        "legacy_crm_identifier": 0.03, "gdpr_consent": 0.91, "hs_lead_status": 0.66,
    },
}

SAMPLE_VALUES = {
    "firstname": ["Amara", "Jonas", "Priya", "Wei", "Tomás"],
    "lastname": ["Osei", "Lindqvist", "Raman", "Chen", "Ferreira"],
    "email": ["a.osei@example.com", "j.lind@example.com", "p.raman@example.com"],
    "phone": ["+44 20 7946 0958", "+1 415 555 0132"],
    "jobtitle": ["Head of Operations", "Procurement Manager", "CTO"],
    "name": ["Northwind Trading", "Acme Logistics", "Harbour Foods Ltd"],
    "dealname": ["Northwind — Q3 Renewal", "Acme — Platform Expansion"],
    "order_number": ["SO-100234", "SO-100235", "SO-100236"],
}


def make_samples():
    """Records shaped so 02 can compute fill rates and size Text fields."""
    samples = {}
    for obj, props in (("contacts", CONTACTS), ("companies", COMPANIES),
                       ("deals", DEALS), ("tickets", TICKETS),
                       ("service_orders", SERVICE_ORDERS)):
        rates = FILL_RATES.get(obj, {})
        records = []
        for i in range(100):
            values = {}
            for p in props:
                name = p["name"]
                if random.random() > rates.get(name, 0.8):
                    continue
                pool = SAMPLE_VALUES.get(name)
                if pool:
                    values[name] = random.choice(pool)
                elif p["type"] == "number":
                    values[name] = str(random.randint(1, 500000))
                elif p["type"] == "bool":
                    values[name] = random.choice(["true", "false"])
                elif p["type"] in ("date", "datetime"):
                    values[name] = "2025-06-1{}T09:00:00Z".format(i % 10)
                elif p.get("options"):
                    values[name] = random.choice(p["options"])["value"]
                else:
                    values[name] = f"sample value {i}"
            records.append({"id": str(1000000 + i), "properties": values})
        samples[obj] = records
    return samples


def main():
    FIXTURES.mkdir(exist_ok=True)

    files = {
        "schemas.json": SCHEMAS,
        "properties_contacts.json": CONTACTS,
        "properties_companies.json": COMPANIES,
        "properties_deals.json": DEALS,
        "properties_tickets.json": TICKETS,
        "properties_service_orders.json": SERVICE_ORDERS,
        "pipelines.json": PIPELINES,
        "owners.json": OWNERS,
        "associations.json": ASSOCIATIONS,
        "samples.json": make_samples(),
        "portal_capabilities.json": {
            "sensitive_data_enabled": "unknown",
            "evidence": "synthetic fixture — no portal was probed",
            "warning": "Fixture data. Re-run 01 against the real portal before Phase 1.",
            "probed_at": datetime.now(timezone.utc).isoformat(),
            "scope_problems": [],
        },
    }

    for name, data in files.items():
        path = FIXTURES / name
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  wrote fixtures/{name}")

    print(f"\n{len(files)} fixtures written.")
    print("Deliberate edge cases included:")
    print("  - 2 calculated properties (contacts.engagement_score, deals.hs_forecast_amount)")
    print("  - 1 name over 40 chars once suffixed (contacts.internal_bio_...)")
    print("  - 1 picklist with 120 options (companies.industry)")
    print("  - 1 property at 3% fill (contacts.legacy_crm_identifier)")
    print("  - 1 archived owner and 1 owner with no email")
    print("  - 1 custom object with a pipeline (service_orders)")
    print("  - 2 many-to-many associations needing junction objects")
    print("\nNEXT: python scripts/01_fetch_hubspot.py --fixture")


if __name__ == "__main__":
    main()
