import datetime

import jwt
import pytest
from apps.authentication.models import RoleChoices, Tenant, TenantMembership, User
from django.conf import settings


@pytest.mark.django_db
class TestSchemaApiIsolationAndLifecycle:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.tenant_alpha = Tenant.objects.create(name="Alpha Corp", slug="alpha-corp")
        self.tenant_beta = Tenant.objects.create(name="Beta Industries", slug="beta-industries")

        self.user_admin = User.objects.create_user(email="admin@alpha.com", password="password")
        self.user_editor = User.objects.create_user(email="editor@alpha.com", password="password")
        self.user_beta = User.objects.create_user(email="admin@beta.com", password="password")

        TenantMembership.objects.create(
            user=self.user_admin, tenant=self.tenant_alpha, role=RoleChoices.ADMIN, is_default=True
        )
        TenantMembership.objects.create(
            user=self.user_editor,
            tenant=self.tenant_alpha,
            role=RoleChoices.EDITOR,
            is_default=True,
        )
        TenantMembership.objects.create(
            user=self.user_beta, tenant=self.tenant_beta, role=RoleChoices.ADMIN, is_default=True
        )

    def _token_for(self, user: User, tenant: Tenant, role: str) -> str:
        now = datetime.datetime.now(datetime.UTC)
        payload = {
            "sub": str(user.id),
            "email": user.email,
            "tenant_id": str(tenant.id),
            "tenant_slug": tenant.slug,
            "role": role,
            "iat": int(now.timestamp()),
            "exp": int((now + datetime.timedelta(seconds=900)).timestamp()),
        }
        return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)

    def test_admin_can_save_draft_and_publish(self, api_client):
        token_admin = self._token_for(self.user_admin, self.tenant_alpha, RoleChoices.ADMIN)
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token_admin}")

        schema_payload = {
            "slug": "invoice",
            "name": "Customer Invoice",
            "fields": [
                {"name": "inv_number", "type": "text", "required": True},
                {"name": "amount", "type": "number", "required": True},
            ],
        }

        # 1. Save draft
        res_draft = api_client.post("/api/v1/schemas/types/", schema_payload, format="json")
        assert res_draft.status_code == 201
        assert res_draft.data["status"] == "draft"
        assert res_draft.data["version"] == 0

        # 2. Publish draft
        res_publish = api_client.post("/api/v1/schemas/types/invoice/publish/")
        assert res_publish.status_code == 200
        assert res_publish.data["status"] == "published"
        assert res_publish.data["version"] == 1

        # 3. Retrieve compiled JSON Schema
        res_compiled = api_client.get("/api/v1/schemas/types/invoice/compiled/")
        assert res_compiled.status_code == 200
        assert res_compiled.data["title"] == "Customer Invoice"
        assert "inv_number" in res_compiled.data["properties"]

    def test_editor_cannot_create_or_publish_schemas(self, api_client):
        token_editor = self._token_for(self.user_editor, self.tenant_alpha, RoleChoices.EDITOR)
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token_editor}")

        res = api_client.post(
            "/api/v1/schemas/types/", {"slug": "test", "fields": []}, format="json"
        )
        assert res.status_code == 403
        assert "Admin role required" in res.data["detail"]

        res_pub = api_client.post("/api/v1/schemas/types/invoice/publish/")
        assert res_pub.status_code == 403

    def test_cross_tenant_schema_access_returns_404(self, api_client):
        # Admin Alpha creates and publishes schema
        token_alpha = self._token_for(self.user_admin, self.tenant_alpha, RoleChoices.ADMIN)
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token_alpha}")
        api_client.post(
            "/api/v1/schemas/types/",
            {
                "slug": "private_deal",
                "fields": [{"name": "deal_val", "type": "number"}],
            },
            format="json",
        )
        api_client.post("/api/v1/schemas/types/private_deal/publish/")

        # Admin Beta attempts to view Alpha's schema
        token_beta = self._token_for(self.user_beta, self.tenant_beta, RoleChoices.ADMIN)
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token_beta}")

        res_get = api_client.get("/api/v1/schemas/types/private_deal/")
        # MUST return 404 Not Found to eliminate resource enumeration
        assert res_get.status_code == 404
        assert "not found" in res_get.data["detail"]

    def test_breaking_change_without_flag_rejected(self, api_client):
        token_admin = self._token_for(self.user_admin, self.tenant_alpha, RoleChoices.ADMIN)
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token_admin}")

        # Version 1: field `title`
        api_client.post(
            "/api/v1/schemas/types/",
            {
                "slug": "article",
                "fields": [{"name": "title", "type": "text", "required": True}],
            },
            format="json",
        )
        api_client.post("/api/v1/schemas/types/article/publish/")

        # Draft Version 2: removes `title` (breaking change)
        api_client.post(
            "/api/v1/schemas/types/",
            {
                "slug": "article",
                "fields": [{"name": "headline", "type": "text", "required": True}],
            },
            format="json",
        )

        # Publish without allow_breaking flag fails
        res_fail = api_client.post(
            "/api/v1/schemas/types/article/publish/",
            {"allow_breaking": False},
            format="json",
        )
        assert res_fail.status_code == 400
        assert "breaking changes" in res_fail.data["detail"]

        # Publish with allow_breaking flag succeeds
        res_ok = api_client.post(
            "/api/v1/schemas/types/article/publish/",
            {"allow_breaking": True},
            format="json",
        )
        assert res_ok.status_code == 200
        assert res_ok.data["version"] == 2
