import datetime

import jwt
import pytest
from apps.authentication.models import RoleChoices, Tenant, TenantMembership, User
from django.conf import settings


@pytest.mark.django_db
class TestFieldMaskingAndRBAC:
    @pytest.fixture(autouse=True)
    def setup_data(self, api_client):
        self.tenant = Tenant.objects.create(name="SecureCorp", slug="securecorp")

        self.user_admin = User.objects.create_user(email="admin@secure.com", password="password")
        self.user_editor = User.objects.create_user(email="editor@secure.com", password="password")
        self.user_viewer = User.objects.create_user(email="viewer@secure.com", password="password")

        TenantMembership.objects.create(
            user=self.user_admin, tenant=self.tenant, role=RoleChoices.ADMIN, is_default=True
        )
        TenantMembership.objects.create(
            user=self.user_editor, tenant=self.tenant, role=RoleChoices.EDITOR, is_default=True
        )
        TenantMembership.objects.create(
            user=self.user_viewer, tenant=self.tenant, role=RoleChoices.VIEWER, is_default=True
        )

        # Publish schema with explicit field permissions
        api_client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {self._token_for(self.user_admin, RoleChoices.ADMIN)}"
        )
        api_client.post(
            "/api/v1/schemas/types/",
            {
                "slug": "payroll",
                "name": "Payroll Record",
                "fields": [
                    {"name": "employee_name", "type": "text", "required": True},
                    {"name": "salary", "type": "number", "required": True},
                    {"name": "ssn", "type": "text", "required": True},
                ],
                "permissions": {
                    "field_permissions": {
                        "salary": {"admin": "write", "editor": "read", "viewer": "hidden"},
                        "ssn": {"admin": "write", "editor": "masked", "viewer": "hidden"},
                    }
                },
            },
            format="json",
        )
        api_client.post("/api/v1/schemas/types/payroll/publish/")

    def _token_for(self, user: User, role: str) -> str:
        now = datetime.datetime.now(datetime.UTC)
        payload = {
            "sub": str(user.id),
            "email": user.email,
            "tenant_id": str(self.tenant.id),
            "tenant_slug": self.tenant.slug,
            "role": role,
            "iat": int(now.timestamp()),
            "exp": int((now + datetime.timedelta(seconds=900)).timestamp()),
        }
        return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)

    def test_viewer_never_sees_hidden_fields(self, api_client):
        """Invariant #6: Viewer querying records never sees masked/hidden fields in detail or list."""
        # 1. Admin creates record
        token_admin = self._token_for(self.user_admin, RoleChoices.ADMIN)
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token_admin}")
        res_create = api_client.post(
            "/api/v1/o/payroll/",
            {"employee_name": "Bob Smith", "salary": 180000, "ssn": "123-45-6789"},
            format="json",
        )
        assert res_create.status_code == 201
        rec_id = res_create.data["id"]

        # 2. Viewer retrieves record detail
        token_viewer = self._token_for(self.user_viewer, RoleChoices.VIEWER)
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token_viewer}")
        res_view = api_client.get(f"/api/v1/o/payroll/{rec_id}/")
        assert res_view.status_code == 200

        data_view = res_view.data["data"]
        assert data_view["employee_name"] == "Bob Smith"
        # salary and ssn MUST BE COMPLETELY ABSENT for Viewer
        assert "salary" not in data_view
        assert "ssn" not in data_view

        # 3. Viewer retrieves record list
        res_list = api_client.get("/api/v1/o/payroll/")
        assert res_list.status_code == 200
        list_data = res_list.data["records"][0]["data"]
        assert "salary" not in list_data
        assert "ssn" not in list_data

    def test_editor_sees_read_and_masked_fields(self, api_client):
        # Admin creates record
        token_admin = self._token_for(self.user_admin, RoleChoices.ADMIN)
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token_admin}")
        res_create = api_client.post(
            "/api/v1/o/payroll/",
            {"employee_name": "Charlie Brown", "salary": 95000, "ssn": "987-65-4321"},
            format="json",
        )
        rec_id = res_create.data["id"]

        # Editor retrieves record
        token_editor = self._token_for(self.user_editor, RoleChoices.EDITOR)
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token_editor}")
        res_edit = api_client.get(f"/api/v1/o/payroll/{rec_id}/")
        assert res_edit.status_code == 200

        data_edit = res_edit.data["data"]
        assert data_edit["employee_name"] == "Charlie Brown"
        # salary has 'read' permission -> unmasked
        assert data_edit["salary"] == 95000
        # ssn has 'masked' permission -> '***MASKED***'
        assert data_edit["ssn"] == "***MASKED***"

    def test_viewer_write_attempt_rejected_with_403(self, api_client):
        # Admin creates record
        token_admin = self._token_for(self.user_admin, RoleChoices.ADMIN)
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token_admin}")
        res_create = api_client.post(
            "/api/v1/o/payroll/",
            {"employee_name": "Dana Scully", "salary": 140000, "ssn": "555-55-5555"},
            format="json",
        )
        rec_id = res_create.data["id"]

        # Viewer attempts to PATCH salary
        token_viewer = self._token_for(self.user_viewer, RoleChoices.VIEWER)
        api_client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {token_viewer}",
            HTTP_IF_MATCH='W/"1"',
        )
        res_patch = api_client.patch(
            f"/api/v1/o/payroll/{rec_id}/",
            {"salary": 200000},
            format="json",
        )
        # MUST return 403 Forbidden
        assert res_patch.status_code == 403
        assert "does not have write access" in res_patch.data["detail"]
