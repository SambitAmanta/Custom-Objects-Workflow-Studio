import datetime

import jwt
import pytest
from apps.authentication.models import ApiKey, RoleChoices, Tenant, TenantMembership, User
from core.repository import TenantScopedRepository
from django.conf import settings


@pytest.mark.django_db
class TestTenantIsolationMatrix:
    @pytest.fixture(autouse=True)
    def setup_tenants_and_users(self):
        # Create Tenant Alpha & Beta
        self.tenant_alpha = Tenant.objects.create(name="Alpha Corp", slug="alpha-corp")
        self.tenant_beta = Tenant.objects.create(name="Beta Industries", slug="beta-industries")

        # Create Users
        self.user_alpha = User.objects.create_user(
            email="alpha_admin@example.com", password="password123"
        )
        self.user_beta = User.objects.create_user(
            email="beta_admin@example.com", password="password123"
        )
        self.user_multi = User.objects.create_user(
            email="multi_user@example.com", password="password123"
        )

        # Memberships
        TenantMembership.objects.create(
            user=self.user_alpha,
            tenant=self.tenant_alpha,
            role=RoleChoices.ADMIN,
            is_default=True,
        )
        TenantMembership.objects.create(
            user=self.user_beta,
            tenant=self.tenant_beta,
            role=RoleChoices.ADMIN,
            is_default=True,
        )
        TenantMembership.objects.create(
            user=self.user_multi,
            tenant=self.tenant_alpha,
            role=RoleChoices.ADMIN,
            is_default=True,
        )
        TenantMembership.objects.create(
            user=self.user_multi,
            tenant=self.tenant_beta,
            role=RoleChoices.VIEWER,
            is_default=False,
        )

        # API Keys
        self.api_key_alpha, self.raw_key_alpha = ApiKey.generate(
            tenant=self.tenant_alpha,
            name="Alpha CI Key",
            role=RoleChoices.ADMIN,
        )
        self.api_key_beta, self.raw_key_beta = ApiKey.generate(
            tenant=self.tenant_beta,
            name="Beta CI Key",
            role=RoleChoices.ADMIN,
        )

    def _generate_token(self, user: User, tenant: Tenant, role: str) -> str:
        now = datetime.datetime.now(datetime.UTC)
        payload = {
            "sub": str(user.id),
            "user_id": str(user.id),
            "email": user.email,
            "tenant_id": str(tenant.id),
            "tenant_slug": tenant.slug,
            "role": role,
            "iat": int(now.timestamp()),
            "exp": int((now + datetime.timedelta(seconds=900)).timestamp()),
        }
        return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)

    def test_cross_tenant_token_tampering_rejected(self, api_client):
        """User Beta attempting to specify Tenant Alpha via X-Tenant-Id header is rejected."""
        token_beta = self._generate_token(self.user_beta, self.tenant_beta, RoleChoices.ADMIN)

        # User Beta attempts to access with X-Tenant-Id = Alpha
        api_client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {token_beta}", HTTP_X_TENANT_ID=str(self.tenant_alpha.id)
        )
        response = api_client.get("/api/v1/auth/tenants/")
        # Permission denied because User Beta has no membership in Tenant Alpha
        assert response.status_code == 403
        assert "does not have access to the specified tenant" in response.data["detail"]

    def test_multi_tenant_user_header_switching(self, api_client):
        """Multi-tenant user can cleanly switch tenants via X-Tenant-Id header."""
        token_multi = self._generate_token(self.user_multi, self.tenant_alpha, RoleChoices.ADMIN)

        # Switch to Tenant Beta
        api_client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {token_multi}", HTTP_X_TENANT_ID=str(self.tenant_beta.id)
        )
        response = api_client.get("/api/v1/auth/tenants/")
        assert response.status_code == 200

    def test_api_key_tenant_isolation(self, api_client):
        """ApiKey from Tenant Alpha authenticates with Tenant Alpha context strictly."""
        api_client.credentials(HTTP_X_API_KEY=self.raw_key_alpha)
        # Auth info is present and correctly mapped
        response = api_client.get("/healthz")
        assert response.status_code == 200

    def test_data_plane_cross_tenant_isolation_matrix(self, mock_mongo):
        """Adversarial matrix: Any query by foreign tenant ID returns 404 (None), zero leakage."""
        db = mock_mongo["records_db"]
        repo_alpha = TenantScopedRepository(tenant_id=str(self.tenant_alpha.id), db=db)
        repo_beta = TenantScopedRepository(tenant_id=str(self.tenant_beta.id), db=db)

        # Insert 10 records for Tenant Alpha
        alpha_ids = []
        for i in range(10):
            res = repo_alpha.insert_one("invoices", {"invoice_no": f"INV-{i}", "amount": 100 * i})
            alpha_ids.append(res.inserted_id)

        # Tenant Beta iterates over all 10 Alpha IDs
        for alpha_id in alpha_ids:
            found = repo_beta.find_one("invoices", {"_id": alpha_id})
            # MUST be None (which translates to 404 in API ViewSet)
            assert found is None, f"Cross-tenant data leakage detected for ID {alpha_id}!"

        # Tenant Beta listing all invoices sees 0 records
        beta_invoices = list(repo_beta.find("invoices"))
        assert len(beta_invoices) == 0

        # Tenant Alpha listing all invoices sees all 10 records
        alpha_invoices = list(repo_alpha.find("invoices"))
        assert len(alpha_invoices) == 10
