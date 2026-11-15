import concurrent.futures
import datetime
import uuid

import jwt
import pytest
from apps.authentication.models import RoleChoices, Tenant, TenantMembership, User
from core.repository import TenantScopedRepository
from django.conf import settings
from rest_framework.test import APIClient


@pytest.mark.django_db(transaction=True)
class TestIdempotencyConcurrency:
    @pytest.fixture(autouse=True)
    def setup_data(self, api_client):
        self.tenant = Tenant.objects.create(name="FinTech Corp", slug="fintech")
        self.user_a = User.objects.create_user(email="alice@fintech.com", password="password")
        self.user_b = User.objects.create_user(email="bob@fintech.com", password="password")

        TenantMembership.objects.create(
            user=self.user_a, tenant=self.tenant, role=RoleChoices.ADMIN, is_default=True
        )
        TenantMembership.objects.create(
            user=self.user_b, tenant=self.tenant, role=RoleChoices.ADMIN, is_default=True
        )

        self.token_a = self._token_for(self.user_a)
        self.token_b = self._token_for(self.user_b)

        # Publish a transfer object schema
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.token_a}")
        api_client.post(
            "/api/v1/schemas/types/",
            {
                "slug": "transfer",
                "name": "Money Transfer",
                "fields": [
                    {"name": "recipient", "type": "text", "required": True},
                    {"name": "amount", "type": "number", "required": True},
                ],
            },
            format="json",
        )
        api_client.post("/api/v1/schemas/types/transfer/publish/")

    def _token_for(self, user: User) -> str:
        now = datetime.datetime.now(datetime.UTC)
        payload = {
            "sub": str(user.id),
            "email": user.email,
            "tenant_id": str(self.tenant.id),
            "tenant_slug": self.tenant.slug,
            "role": RoleChoices.ADMIN,
            "iat": int(now.timestamp()),
            "exp": int((now + datetime.timedelta(seconds=900)).timestamp()),
        }
        return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)

    def test_50_concurrent_identical_requests_produce_one_record(self, mock_mongo):
        """Invariant #2: 50 concurrent identical requests with Idempotency-Key create exactly 1 record,
        with 49 identical cached replays.
        """
        idempotency_key = f"key_{uuid.uuid4().hex}"
        payload = {"recipient": "Alice Corp", "amount": 5000}

        def make_request():
            client = APIClient()
            client.credentials(
                HTTP_AUTHORIZATION=f"Bearer {self.token_a}",
                HTTP_IDEMPOTENCY_KEY=idempotency_key,
            )
            return client.post("/api/v1/o/transfer/", payload, format="json")

        # Spawn 50 concurrent threads executing simultaneously
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(make_request) for _ in range(50)]
            responses = [f.result() for f in futures]

        # 1. Assert all 50 responses returned HTTP 201 Created
        for res in responses:
            assert res.status_code == 201

        # 2. Assert all 50 responses returned the exact same record ID
        created_ids = {r.data["id"] for r in responses}
        assert len(created_ids) == 1, (
            f"Expected 1 unique record ID, got {len(created_ids)}: {created_ids}"
        )

        # 3. Assert exactly 1 document exists in MongoDB records collection
        repo = TenantScopedRepository(tenant_id=str(self.tenant.id))
        records_in_db = list(repo.find("records", {"type": "transfer"}))
        assert len(records_in_db) == 1, f"Expected 1 record in database, found {len(records_in_db)}"

        # 4. Assert exactly 1 document in idempotency_keys collection
        keys_in_db = list(repo.find("idempotency_keys", {"key": idempotency_key}))
        assert len(keys_in_db) == 1

    def test_idempotency_key_payload_mismatch_returns_422(self, api_client):
        """Reusing the same Idempotency-Key with a different request payload returns 422 Unprocessable Entity."""
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.token_a}")
        key = f"key_{uuid.uuid4().hex}"

        # Request 1: Original payload
        res1 = api_client.post(
            "/api/v1/o/transfer/",
            {"recipient": "Charlie", "amount": 100},
            format="json",
            HTTP_IDEMPOTENCY_KEY=key,
        )
        assert res1.status_code == 201

        # Request 2: Different payload with same Idempotency-Key
        res2 = api_client.post(
            "/api/v1/o/transfer/",
            {"recipient": "Mallory (Attacker)", "amount": 999999},
            format="json",
            HTTP_IDEMPOTENCY_KEY=key,
        )
        assert res2.status_code == 422
        assert "different request body" in res2.data["detail"]

    def test_principal_scoping_prevents_cross_user_key_clashes(self, api_client):
        """Idempotency keys are scoped by (tenant, principal_id, key) so two users can use identical keys."""
        shared_key = "idemp_client_fixed_key"

        # User A sends request with shared_key
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.token_a}")
        res_a = api_client.post(
            "/api/v1/o/transfer/",
            {"recipient": "User A Destination", "amount": 250},
            format="json",
            HTTP_IDEMPOTENCY_KEY=shared_key,
        )
        assert res_a.status_code == 201

        # User B sends request with the exact same shared_key
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.token_b}")
        res_b = api_client.post(
            "/api/v1/o/transfer/",
            {"recipient": "User B Destination", "amount": 350},
            format="json",
            HTTP_IDEMPOTENCY_KEY=shared_key,
        )
        assert res_b.status_code == 201
        # Asserts User B got their own separate record, not User A's data
        assert res_b.data["id"] != res_a.data["id"]
        assert res_b.data["data"]["recipient"] == "User B Destination"
