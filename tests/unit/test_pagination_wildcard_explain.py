import datetime

import jwt
import pytest
from apps.authentication.models import RoleChoices, Tenant, TenantMembership, User
from core.repository import TenantScopedRepository
from django.conf import settings
from rest_framework.test import APIClient


@pytest.mark.django_db
class TestPaginationAndWildcardIndexing:
    """Verifies composite cursor pagination and MongoDB compound wildcard index initialization."""

    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.tenant = Tenant.objects.create(name="Retail Corp", slug="retail-corp")
        self.user = User.objects.create_user(email="admin@retail.com", password="password")
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

    def test_composite_cursor_pagination_page_traversal(self, mock_mongo):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.token}")

        # 1. Publish inventory object type
        client.post(
            "/api/v1/schemas/types/",
            {
                "slug": "item",
                "name": "Inventory Item",
                "fields": [
                    {"name": "sku", "type": "text", "required": True},
                    {"name": "qty", "type": "number", "required": True},
                ],
            },
            format="json",
        )
        client.post("/api/v1/schemas/types/item/publish/")

        # 2. Insert 12 records
        for i in range(12):
            res = client.post(
                "/api/v1/o/item/",
                {"sku": f"SKU-{i:03d}", "qty": 10 + i},
                format="json",
            )
            assert res.status_code == 201

        # 3. Page 1: limit=5
        page_1 = client.get("/api/v1/o/item/?limit=5")
        assert page_1.status_code == 200
        assert len(page_1.data["records"]) == 5
        assert page_1.data["has_next"] is True
        cursor_1 = page_1.data["next_cursor"]
        assert cursor_1 is not None

        page_1_ids = [r["id"] for r in page_1.data["records"]]

        # 4. Page 2: follow cursor_1 with limit=5
        page_2 = client.get(f"/api/v1/o/item/?limit=5&cursor={cursor_1}")
        assert page_2.status_code == 200
        assert len(page_2.data["records"]) == 5
        assert page_2.data["has_next"] is True
        cursor_2 = page_2.data["next_cursor"]

        page_2_ids = [r["id"] for r in page_2.data["records"]]
        # Assert zero overlap between Page 1 and Page 2
        assert len(set(page_1_ids).intersection(set(page_2_ids))) == 0

        # 5. Page 3: follow cursor_2 with limit=5
        page_3 = client.get(f"/api/v1/o/item/?limit=5&cursor={cursor_2}")
        assert page_3.status_code == 200
        assert len(page_3.data["records"]) == 2  # remaining records (12 - 10 = 2)
        assert page_3.data["has_next"] is False

    def test_wildcard_compound_index_initialization(self, mock_mongo):
        from core.unit_of_work import UnitOfWork

        repo = TenantScopedRepository(tenant_id=str(self.tenant.id))
        UnitOfWork(repo)
        indexes = repo.db["records"].index_information()

        # Verify compound base or wildcard indexing definition
        index_keys = [info["key"] for info in indexes.values()]
        tenant_type_indexed = any(
            any(k[0] == "tenant_id" for k in keys) and any(k[0] == "type" for k in keys)
            for keys in index_keys
        )
        assert tenant_type_indexed, f"Expected tenant_id + type indexing, found: {indexes}"
