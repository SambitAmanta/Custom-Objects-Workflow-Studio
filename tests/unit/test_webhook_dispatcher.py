import hashlib
import hmac
import json
import uuid

import httpx
import mongomock
import pytest
from apps.authentication.models import RoleChoices, Tenant
from apps.schemas.services import SchemaRegistryService
from apps.webhooks.models import WebhookEndpoint
from core.repository import TenantScopedRepository
from workers.consumers.webhook_dispatcher import (
    SSRFSecurityViolation,
    WebhookDispatcherConsumer,
)


@pytest.mark.django_db
class TestWebhookDispatcher:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.tenant = Tenant.objects.create(name="Webhook Corp", slug="webhook-corp")
        self.mongo_client = mongomock.MongoClient()
        self.repo = TenantScopedRepository(
            tenant_id=str(self.tenant.id), mongo_client=self.mongo_client
        )
        self.schema_service = SchemaRegistryService(self.repo)

    def test_ssrf_protection_rejects_private_and_metadata_ips(self):
        # Loopback
        with pytest.raises(SSRFSecurityViolation, match="Insecure protocol|SSRF Protection"):
            WebhookDispatcherConsumer.validate_and_resolve_url("https://127.0.0.1/hook")

        # AWS Instance Metadata IP (169.254.169.254)
        with pytest.raises(SSRFSecurityViolation, match="SSRF Protection"):
            WebhookDispatcherConsumer.validate_and_resolve_url(
                "https://169.254.169.254/latest/meta-data/"
            )

        # Private RFC 1918 addresses
        with pytest.raises(SSRFSecurityViolation, match="SSRF Protection"):
            WebhookDispatcherConsumer.validate_and_resolve_url("https://10.0.0.1/webhook")

        with pytest.raises(SSRFSecurityViolation, match="SSRF Protection"):
            WebhookDispatcherConsumer.validate_and_resolve_url("https://192.168.1.50/webhook")

        # Reject HTTP (require HTTPS)
        with pytest.raises(SSRFSecurityViolation, match="Insecure protocol 'http'"):
            WebhookDispatcherConsumer.validate_and_resolve_url("http://example.com/webhook")

    def test_anti_replay_hmac_signature(self):
        secret = "super-secret-key-12345"
        payload = {"event_id": "evt-123", "action": "created"}
        payload_bytes = json.dumps(payload, sort_keys=True).encode("utf-8")
        timestamp = 1700000000

        sig_header = WebhookDispatcherConsumer.generate_signature(
            secret=secret, payload_bytes=payload_bytes, timestamp=timestamp
        )

        assert sig_header.startswith("t=1700000000,v1=")
        expected_sig = hmac.new(
            secret.encode("utf-8"), b"1700000000." + payload_bytes, hashlib.sha256
        ).hexdigest()
        assert sig_header == f"t=1700000000,v1={expected_sig}"

        # Tampering with payload fails validation
        tampered_bytes = json.dumps(
            {"event_id": "evt-123", "action": "deleted"}, sort_keys=True
        ).encode("utf-8")
        tampered_sig = hmac.new(
            secret.encode("utf-8"), b"1700000000." + tampered_bytes, hashlib.sha256
        ).hexdigest()
        assert tampered_sig != expected_sig

    def test_webhook_payload_masks_sensitive_fields_for_viewer(self):
        # 1. Publish customer schema with hidden tax_id field
        self.schema_service.save_draft(
            {
                "slug": "customer",
                "name": "Customer Profile",
                "fields": [
                    {"name": "name", "type": "text", "required": True},
                    {"name": "tax_id", "type": "text", "required": False},
                ],
                "permissions": {
                    "field_permissions": {
                        "tax_id": {"viewer": "hidden", "editor": "masked"},
                    }
                },
            }
        )
        self.schema_service.publish_draft("customer")

        # 2. Insert record with tax_id into MongoDB
        record_id = uuid.uuid4().hex
        self.repo.insert_one(
            "records",
            {
                "_id": record_id,
                "tenant_id": str(self.tenant.id),
                "type": "customer",
                "version": 1,
                "data": {
                    "name": "Acme Global",
                    "tax_id": "SECRET-TAX-9988",
                },
                "state": "active",
                "tombstone": None,
            },
        )

        # 3. Create WebhookEndpoint with VIEWER role
        WebhookEndpoint.objects.create(
            tenant=self.tenant,
            url="https://mock-external-webhook.com/events",
            role=RoleChoices.VIEWER,
            topics=["records.customer.*"],
            is_active=True,
        )

        # 4. Mock HTTP transport to capture dispatched request
        dispatched_requests = []

        def mock_handler(request: httpx.Request):
            dispatched_requests.append(request)
            return httpx.Response(200, json={"status": "received"})

        mock_transport = httpx.MockTransport(mock_handler)
        mock_client = httpx.Client(transport=mock_transport)

        consumer = WebhookDispatcherConsumer(
            mongo_client=self.mongo_client,
            db_name="forge_tenant_data",
            http_client=mock_client,
            allow_insecure_http=False,
        )

        # Mock validate_and_resolve_url so mock-external-webhook.com resolves to public IP
        consumer.validate_and_resolve_url = lambda url, allow_insecure_http=False: (
            "93.184.216.34",
            "mock-external-webhook.com",
            443,
        )

        event = {
            "event_id": uuid.uuid4().hex,
            "tenant_id": str(self.tenant.id),
            "topic": "records.customer.created",
            "action": "created",
            "record_id": record_id,
            "version": 1,
        }

        # 5. Dispatch
        consumer.handle_event(event)

        assert len(dispatched_requests) == 1
        req = dispatched_requests[0]
        body = json.loads(req.content.decode("utf-8"))

        # Invariant: Sensitive tax_id MUST BE STRIPPED from viewer webhook payload
        assert "name" in body["record"]["data"]
        assert body["record"]["data"]["name"] == "Acme Global"
        assert "tax_id" not in body["record"]["data"]

        # Signature header is present and valid
        assert "X-Forge-Signature" in req.headers
        sig_header = req.headers["X-Forge-Signature"]
        assert sig_header.startswith("t=") and ",v1=" in sig_header
