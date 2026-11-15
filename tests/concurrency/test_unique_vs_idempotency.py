import datetime
import uuid

import jwt
import pytest
from apps.authentication.models import RoleChoices, Tenant, TenantMembership, User
from core.repository import TenantScopedRepository
from django.conf import settings
from rest_framework.test import APIClient


@pytest.mark.django_db(transaction=True)
class TestUniqueVsIdempotencyDisambiguation:
    """Explicitly verifies the disambiguation between:

    1. True idempotency replay (identical key + identical payload -> 200/201 cached replay).
    2. Idempotency payload mismatch (identical key + different payload -> 422 Unprocessable Entity).
    3. Unique constraint collision (different key / requests + duplicate unique field -> 409 Conflict).
    """

    @pytest.fixture(autouse=True)
    def setup_data(self, api_client):
        self.tenant = Tenant.objects.create(name="FinTech Corp", slug="fintech-disambig")
        self.user = User.objects.create_user(email="user@fintech.com", password="password")
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

        # Publish a schema with a unique email field
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.token}")
        api_client.post(
            "/api/v1/schemas/types/",
            {
                "slug": "member",
                "name": "Member Profile",
                "fields": [
                    {"name": "full_name", "type": "text", "required": True},
                    {"name": "email", "type": "text", "required": True, "unique": True},
                ],
            },
            format="json",
        )
        api_client.post("/api/v1/schemas/types/member/publish/")

    def test_idempotent_replay_vs_unique_collision(self, mock_mongo):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.token}")

        idempotency_key = f"key-{uuid.uuid4().hex}"
        member_payload = {
            "full_name": "Satoshi Nakamoto",
            "email": "satoshi@gmx.com",
        }

        # 1. Initial creation succeeds (201 Created)
        res_initial = client.post(
            "/api/v1/o/member/",
            member_payload,
            format="json",
            HTTP_IDEMPOTENCY_KEY=idempotency_key,
        )
        assert res_initial.status_code == 201
        initial_id = res_initial.data["id"]

        # 2. Idempotent Retry: Same idempotency key + same payload -> 201 Cached Replay
        res_replay = client.post(
            "/api/v1/o/member/",
            member_payload,
            format="json",
            HTTP_IDEMPOTENCY_KEY=idempotency_key,
        )
        assert res_replay.status_code == 201
        assert res_replay.data["id"] == initial_id
        assert res_replay.headers.get("X-Idempotent-Replay") == "true"

        # Assert only 1 record exists in MongoDB
        repo = TenantScopedRepository(tenant_id=str(self.tenant.id))
        count = len(list(repo.find("records", {"type": "member"})))
        assert count == 1

        # 3. Idempotency Payload Mismatch: Same key + different payload -> 422 Unprocessable Entity
        res_mismatch = client.post(
            "/api/v1/o/member/",
            {"full_name": "Hal Finney", "email": "hal@finney.org"},
            format="json",
            HTTP_IDEMPOTENCY_KEY=idempotency_key,
        )
        assert res_mismatch.status_code == 422
        assert "different request body" in res_mismatch.data["detail"].lower()

        # 4. True Unique Constraint Collision: Different key + duplicate email -> 409 Conflict
        different_key = f"key-{uuid.uuid4().hex}"
        res_collision = client.post(
            "/api/v1/o/member/",
            {
                "full_name": "Fake Satoshi",
                "email": "satoshi@gmx.com",  # Duplicate unique email!
            },
            format="json",
            HTTP_IDEMPOTENCY_KEY=different_key,
        )
        assert res_collision.status_code == 409
        assert "unique field 'email'" in res_collision.data["detail"].lower()
        assert res_collision.data.get("field") == "email"
