import datetime

import jwt
import pytest
from apps.authentication.models import RoleChoices, Tenant, TenantMembership, User
from django.conf import settings
from rest_framework.test import APIClient


@pytest.mark.django_db
class TestSchemaEvolutionAndLegacyRecords:
    """Verifies that schema version upgrades maintain backward compatibility

    with legacy records created under earlier schema versions.
    """

    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.tenant = Tenant.objects.create(name="SaaS Corp", slug="saas-corp")
        self.user = User.objects.create_user(email="admin@saas.com", password="password")
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

    def test_legacy_record_readable_and_updatable_after_schema_bump(self, mock_mongo):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.token}")

        # 1. Publish Schema v1
        client.post(
            "/api/v1/schemas/types/",
            {
                "slug": "subscription",
                "name": "Subscription",
                "fields": [
                    {"name": "plan_name", "type": "text", "required": True},
                    {"name": "monthly_price", "type": "number", "required": True},
                ],
            },
            format="json",
        )
        pub_v1 = client.post("/api/v1/schemas/types/subscription/publish/")
        assert pub_v1.status_code == 200
        assert pub_v1.data["version"] == 1

        # 2. Create record under v1
        rec_res = client.post(
            "/api/v1/o/subscription/",
            {"plan_name": "Enterprise Pro", "monthly_price": 499},
            format="json",
        )
        assert rec_res.status_code == 201
        record_id = rec_res.data["id"]
        assert rec_res.data["schema_version"] == 1
        assert rec_res.data["version"] == 1

        # 3. Evolve Schema to v2: Add non-breaking optional field 'auto_renew'
        client.post(
            "/api/v1/schemas/types/",
            {
                "slug": "subscription",
                "name": "Subscription",
                "fields": [
                    {"name": "plan_name", "type": "text", "required": True},
                    {"name": "monthly_price", "type": "number", "required": True},
                    {"name": "auto_renew", "type": "boolean", "required": False, "default": True},
                ],
            },
            format="json",
        )
        pub_v2 = client.post("/api/v1/schemas/types/subscription/publish/")
        assert pub_v2.status_code == 200
        assert pub_v2.data["version"] == 2

        # 4. Read legacy record created under v1: must remain fully readable
        get_res = client.get(f"/api/v1/o/subscription/{record_id}/")
        assert get_res.status_code == 200
        assert get_res.data["id"] == record_id
        assert get_res.data["data"]["plan_name"] == "Enterprise Pro"
        assert get_res.data["schema_version"] == 1  # Retains provenance

        # 5. Patch legacy record with new v2 field using ETag
        patch_res = client.patch(
            f"/api/v1/o/subscription/{record_id}/",
            {"auto_renew": False},
            format="json",
            HTTP_IF_MATCH='W/"1"',
        )
        assert patch_res.status_code == 200
        assert patch_res.data["version"] == 2
        assert patch_res.data["data"]["auto_renew"] is False
