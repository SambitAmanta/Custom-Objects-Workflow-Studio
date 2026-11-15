import datetime

import jwt
import pytest
from apps.authentication.models import RoleChoices, Tenant, TenantMembership, User
from django.conf import settings
from django.core.cache import cache
from rest_framework import status
from rest_framework.test import APIClient


@pytest.mark.django_db
class TestTenantRateThrottling:
    """Verifies per-tenant rate throttling under high request bursts."""

    @pytest.fixture(autouse=True)
    def setup_data(self):
        cache.clear()
        self.tenant = Tenant.objects.create(name="Burst Corp", slug="burst-corp")
        self.user = User.objects.create_user(email="burst@corp.com", password="password")
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

    def test_tenant_rate_throttle_blocks_excess_requests(self, monkeypatch, mock_mongo):
        from rest_framework.settings import api_settings

        # Set throttle limit to 3 requests per minute
        monkeypatch.setitem(api_settings.DEFAULT_THROTTLE_RATES, "tenant", "3/min")

        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.token}")

        # Publish a minimal type
        client.post(
            "/api/v1/schemas/types/",
            {
                "slug": "ping",
                "name": "Ping",
                "fields": [{"name": "msg", "type": "text"}],
            },
            format="json",
        )
        client.post("/api/v1/schemas/types/ping/publish/")

        # Clear throttle cache so our burst test starts with 0 consumed requests
        cache.clear()

        # Send 3 allowed requests
        for i in range(3):
            res = client.get("/api/v1/o/ping/")
            assert res.status_code == status.HTTP_200_OK, (
                f"Request {i + 1} failed with {res.status_code}"
            )

        # 4th request must be throttled with HTTP 429
        throttled_res = client.get("/api/v1/o/ping/")
        assert throttled_res.status_code == status.HTTP_429_TOO_MANY_REQUESTS
        assert "throttled" in throttled_res.data["detail"].lower()
        assert "Retry-After" in throttled_res.headers
