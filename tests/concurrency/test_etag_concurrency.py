import datetime

import jwt
import pytest
from apps.authentication.models import RoleChoices, Tenant, TenantMembership, User
from django.conf import settings


@pytest.mark.django_db
class TestETagConcurrencyAndLostUpdates:
    @pytest.fixture(autouse=True)
    def setup_data(self, api_client):
        self.tenant = Tenant.objects.create(name="Acme Corp", slug="acme")
        self.user = User.objects.create_user(email="editor@acme.com", password="password")
        TenantMembership.objects.create(
            user=self.user, tenant=self.tenant, role=RoleChoices.ADMIN, is_default=True
        )

        now = datetime.datetime.now(datetime.UTC)
        payload = {
            "sub": str(self.user.id),
            "email": self.user.email,
            "tenant_id": str(self.tenant.id),
            "tenant_slug": self.tenant.slug,
            "role": RoleChoices.ADMIN,
            "iat": int(now.timestamp()),
            "exp": int((now + datetime.timedelta(seconds=900)).timestamp()),
        }
        self.token = jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.token}")

        # Publish a test schema
        api_client.post(
            "/api/v1/schemas/types/",
            {
                "slug": "ticket",
                "name": "Support Ticket",
                "fields": [
                    {"name": "title", "type": "text", "required": True},
                    {"name": "status", "type": "text", "default": "open"},
                ],
            },
            format="json",
        )
        api_client.post("/api/v1/schemas/types/ticket/publish/")

    def test_missing_if_match_returns_428(self, api_client):
        """RFC 6585: Updating without If-Match header returns 428 Precondition Required."""
        res_create = api_client.post(
            "/api/v1/o/ticket/",
            {"title": "Initial Title"},
            format="json",
        )
        record_id = res_create.data["id"]

        # Attempt PATCH without If-Match header
        res_patch = api_client.patch(
            f"/api/v1/o/ticket/{record_id}/",
            {"title": "Updated Title"},
            format="json",
        )
        assert res_patch.status_code == 428
        assert "If-Match header is required" in res_patch.data["detail"]

    def test_stale_etag_returns_412_precondition_failed(self, api_client):
        """Invariant #3: Concurrent updates with a stale ETag return 412 Precondition Failed."""
        res_create = api_client.post(
            "/api/v1/o/ticket/",
            {"title": "Initial Title"},
            format="json",
        )
        record_id = res_create.data["id"]
        assert res_create.headers["ETag"] == 'W/"1"'

        # Client A reads initial version 1
        etag_client_a = 'W/"1"'
        # Client B also reads initial version 1
        etag_client_b = 'W/"1"'

        # Client A updates with If-Match: W/"1" -> Succeeded, bumps version to 2
        api_client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
            HTTP_IF_MATCH=etag_client_a,
        )
        res_a = api_client.patch(
            f"/api/v1/o/ticket/{record_id}/",
            {"title": "Client A Edit"},
            format="json",
        )
        assert res_a.status_code == 200
        assert res_a.headers["ETag"] == 'W/"2"'
        assert res_a.data["version"] == 2

        # Client B attempts update with stale If-Match: W/"1" -> strictly 412!
        api_client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
            HTTP_IF_MATCH=etag_client_b,
        )
        res_b = api_client.patch(
            f"/api/v1/o/ticket/{record_id}/",
            {"title": "Client B Edit (Lost Race)"},
            format="json",
        )
        assert res_b.status_code == 412
        assert "Version mismatch" in res_b.data["detail"]
