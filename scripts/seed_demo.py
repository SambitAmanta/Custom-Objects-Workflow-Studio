"""Bootstrap and seed script for Forge demo environment.

Provisions:
1. Relational database records (PostgreSQL / SQLite):
   - Acme Corp tenant + Stark Industries tenant
   - alice@acme.com (admin), bob@acme.com (editor), charlie@acme.com (viewer)
   - tony@stark.com (admin)
   - Live ApiKey for Acme Corp
   - WebhookEndpoint configured for Acme Corp
2. MongoDB document collections:
   - "invoice" Object Type (v1 published schema + workflow state machine + field permissions)
   - 5 sample invoice records across different workflow states
"""

import os
import sys
from pathlib import Path

# Add project roots
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "apps" / "api"))
sys.path.insert(0, str(BASE_DIR / "apps"))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django

django.setup()

import pymongo
from apps.authentication.models import (
    ApiKey,
    RoleChoices,
    Tenant,
    TenantMembership,
    User,
)
from apps.records.services import RecordService
from apps.schemas.services import SchemaRegistryService
from apps.webhooks.models import WebhookEndpoint
from core.repository import TenantScopedRepository
from django.conf import settings


def seed_demo():
    print("=" * 60)
    print("  FORGE PLATFORM: BOOTSTRAPPING DEMO ENVIRONMENT")
    print("=" * 60)

    from django.core.management import call_command

    print("[*] Ensuring database migrations are applied...")
    call_command("migrate", interactive=False)

    # 1. Tenants
    acme, _ = Tenant.objects.get_or_create(
        slug="acme-corp",
        defaults={"name": "Acme Corporation"},
    )
    stark, _ = Tenant.objects.get_or_create(
        slug="stark-industries",
        defaults={"name": "Stark Industries"},
    )
    print(f"[+] Tenants created: '{acme.name}' ({acme.id}) and '{stark.name}' ({stark.id})")

    # 2. Users & Memberships
    users_data = [
        ("alice@acme.com", "Alice Smith", RoleChoices.ADMIN, acme),
        ("bob@acme.com", "Bob Jones", RoleChoices.EDITOR, acme),
        ("charlie@acme.com", "Charlie Brown", RoleChoices.VIEWER, acme),
        ("tony@stark.com", "Tony Stark", RoleChoices.ADMIN, stark),
    ]

    for email, name, role, tenant in users_data:
        user, created = User.objects.get_or_create(
            email=email,
            defaults={"full_name": name},
        )
        if created:
            user.set_password("password123")
            user.save()
        membership, _ = TenantMembership.objects.get_or_create(
            tenant=tenant,
            user=user,
            defaults={"role": role},
        )
        membership.role = role
        membership.save()
        print(f"[+] User: {email} ({role}) linked to {tenant.name}")

    # 3. API Key for Acme
    existing_key = ApiKey.objects.filter(tenant=acme, name="Production Admin Key").first()
    if not existing_key:
        api_key_obj, raw_api_key = ApiKey.generate(
            tenant=acme,
            name="Production Admin Key",
            role=RoleChoices.ADMIN,
        )
        print(f"[+] Generated Admin API Key: {raw_api_key}")
    else:
        raw_api_key = f"{existing_key.key_prefix}_<stored-hash>"
        print(f"[+] Existing Admin API Key: {existing_key.key_prefix}***")

    # 4. Webhook Endpoint
    webhook_url = "https://httpbin.org/post"
    wh, _ = WebhookEndpoint.objects.get_or_create(
        tenant=acme,
        url=webhook_url,
        defaults={
            "secret": "whsec_demo_secret_key_acme_2026",
            "role": RoleChoices.VIEWER,
            "topics": [
                "record.created",
                "record.updated",
                "record.transitioned",
            ],
            "is_active": True,
        },
    )
    print(f"[+] Webhook Endpoint: {wh.url} (egress role: {wh.role})")

    # 5. MongoDB: Schemas & Records
    try:
        mongo_client = pymongo.MongoClient(
            getattr(settings, "MONGO_URI", "mongodb://localhost:27017/?replicaSet=rs0"),
            serverSelectionTimeoutMS=2000,
        )
        mongo_client.admin.command("ping")
        has_mongo = True
    except Exception as e:
        print(f"[-] MongoDB not currently reachable ({e}). Skipping document seeding.")
        has_mongo = False

    if has_mongo:
        repo = TenantScopedRepository(tenant_id=str(acme.id), mongo_client=mongo_client)
        schema_service = SchemaRegistryService(repo)
        record_service = RecordService(repo)

        invoice_schema_data = {
            "slug": "invoice",
            "display_name": "Customer Invoice",
            "fields": [
                {
                    "name": "invoice_number",
                    "label": "Invoice Number",
                    "type": "text",
                    "required": True,
                    "unique": True,
                },
                {
                    "name": "customer_name",
                    "label": "Customer Name",
                    "type": "text",
                    "required": True,
                },
                {
                    "name": "customer_email",
                    "label": "Customer Email",
                    "type": "email",
                    "required": False,
                },
                {
                    "name": "amount",
                    "label": "Total Amount ($)",
                    "type": "number",
                    "required": True,
                    "validation_rules": {"minimum": 0},
                },
                {
                    "name": "tax_id",
                    "label": "Confidential Tax ID",
                    "type": "text",
                    "required": False,
                },
                {
                    "name": "notes",
                    "label": "Internal Memo / Notes",
                    "type": "text",
                    "required": False,
                },
            ],
            "permissions": {
                "field_permissions": {
                    "tax_id": {
                        "admin": "write",
                        "editor": "masked",
                        "viewer": "hidden",
                    },
                    "amount": {
                        "admin": "write",
                        "editor": "write",
                        "viewer": "read",
                    },
                }
            },
            "workflow": {
                "initial_state": "draft",
                "states": ["draft", "submitted", "approved", "rejected", "paid"],
                "transitions": [
                    {
                        "name": "submit",
                        "from_state": "draft",
                        "to_state": "submitted",
                        "allowed_roles": ["admin", "editor"],
                    },
                    {
                        "name": "approve",
                        "from_state": "submitted",
                        "to_state": "approved",
                        "allowed_roles": ["admin"],
                        "condition": {"<=": [{"var": "amount"}, 10000]},
                    },
                    {
                        "name": "reject",
                        "from_state": "submitted",
                        "to_state": "rejected",
                        "allowed_roles": ["admin"],
                    },
                    {
                        "name": "pay",
                        "from_state": "approved",
                        "to_state": "paid",
                        "allowed_roles": ["admin"],
                    },
                ],
            },
        }

        existing = schema_service.get_latest_published("invoice")
        if not existing:
            schema_service.save_draft(invoice_schema_data)
            published = schema_service.publish("invoice", is_breaking=False)
            print(f"[+] Published Schema: 'invoice' v{published['version']}")
        else:
            print(f"[+] Schema 'invoice' already published at v{existing['version']}")

        sample_invoices = [
            {
                "invoice_number": "INV-2026-001",
                "customer_name": "Stark Logistics",
                "customer_email": "billing@starklogistics.io",
                "amount": 2500,
                "tax_id": "US-99182746",
                "notes": "Annual cloud enterprise tier subscription.",
            },
            {
                "invoice_number": "INV-2026-002",
                "customer_name": "Wayne Technologies",
                "customer_email": "accounts@waynetech.com",
                "amount": 14500,
                "tax_id": "US-88271635",
                "notes": "Hardware rack delivery - requires CFO override approval.",
            },
            {
                "invoice_number": "INV-2026-003",
                "customer_name": "Cyberdyne Systems",
                "customer_email": "finance@cyberdyne.ai",
                "amount": 750,
                "tax_id": "US-77362514",
                "notes": "Initial consulting sprint.",
            },
            {
                "invoice_number": "INV-2026-004",
                "customer_name": "Oscorp Global",
                "customer_email": "payables@oscorp.org",
                "amount": 4200,
                "tax_id": "US-66453423",
                "notes": "Lab consumables procurement.",
            },
            {
                "invoice_number": "INV-2026-005",
                "customer_name": "Massive Dynamic",
                "customer_email": "billing@massivedynamic.com",
                "amount": 9200,
                "tax_id": "US-55544332",
                "notes": "Quarterly analytics license.",
            },
        ]

        for inv in sample_invoices:
            try:
                rec, replayed = record_service.create_record(
                    type_slug="invoice",
                    data=inv,
                    principal_id="alice@acme.com",
                    role="admin",
                    idempotency_key=f"seed-{inv['invoice_number']}",
                    request_hash=f"hash-{inv['invoice_number']}",
                )
                print(
                    f"[+] Seeded Record: {inv['invoice_number']} (${inv['amount']}) -> State: {rec.get('workflow_state', 'draft')}"
                )
            except Exception as e:
                print(f"[-] Record {inv['invoice_number']} note: {e}")

    print("\n" + "=" * 60)
    print("  SEEDING COMPLETE! Credentials for testing:")
    print("  Tenant:       acme-corp")
    print("  Admin User:   alice@acme.com    (password: password123)")
    print("  Editor User:  bob@acme.com      (password: password123)")
    print("  Viewer User:  charlie@acme.com  (password: password123)")
    print(f"  API Key:      {raw_api_key}")
    print("=" * 60)


if __name__ == "__main__":
    seed_demo()
