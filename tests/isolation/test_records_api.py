import datetime

import jwt
import pytest
from apps.authentication.models import RoleChoices, Tenant, TenantMembership, User
from django.conf import settings


@pytest.mark.django_db
class TestRecordsApiIsolationAndQueries:
    @pytest.fixture(autouse=True)
    def setup_data(self, api_client):
        self.tenant_alpha = Tenant.objects.create(name="Alpha Corp", slug="alpha")
        self.tenant_beta = Tenant.objects.create(name="Beta Industries", slug="beta")

        self.user_alpha = User.objects.create_user(email="admin@alpha.com", password="password")
        self.user_beta = User.objects.create_user(email="admin@beta.com", password="password")

        TenantMembership.objects.create(
            user=self.user_alpha, tenant=self.tenant_alpha, role=RoleChoices.ADMIN, is_default=True
        )
        TenantMembership.objects.create(
            user=self.user_beta, tenant=self.tenant_beta, role=RoleChoices.ADMIN, is_default=True
        )

        now = datetime.datetime.now(datetime.UTC)
        payload_a = {
            "sub": str(self.user_alpha.id),
            "email": self.user_alpha.email,
            "tenant_id": str(self.tenant_alpha.id),
            "tenant_slug": self.tenant_alpha.slug,
            "role": RoleChoices.ADMIN,
            "iat": int(now.timestamp()),
            "exp": int((now + datetime.timedelta(seconds=900)).timestamp()),
        }
        self.token_alpha = jwt.encode(
            payload_a, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM
        )

        payload_b = {
            "sub": str(self.user_beta.id),
            "email": self.user_beta.email,
            "tenant_id": str(self.tenant_beta.id),
            "tenant_slug": self.tenant_beta.slug,
            "role": RoleChoices.ADMIN,
            "iat": int(now.timestamp()),
            "exp": int((now + datetime.timedelta(seconds=900)).timestamp()),
        }
        self.token_beta = jwt.encode(
            payload_b, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM
        )

        # Publish Department and Employee schemas in Tenant Alpha
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.token_alpha}")
        api_client.post(
            "/api/v1/schemas/types/",
            {
                "slug": "department",
                "name": "Department",
                "fields": [{"name": "name", "type": "text", "required": True}],
            },
            format="json",
        )
        api_client.post("/api/v1/schemas/types/department/publish/")

        api_client.post(
            "/api/v1/schemas/types/",
            {
                "slug": "employee",
                "name": "Employee",
                "fields": [
                    {"name": "name", "type": "text", "required": True},
                    {"name": "score", "type": "number"},
                    {
                        "name": "status",
                        "type": "enum",
                        "options": ["active", "suspended", "terminated"],
                    },
                    {"name": "dept_ref", "type": "reference", "target_type": "department"},
                ],
            },
            format="json",
        )
        api_client.post("/api/v1/schemas/types/employee/publish/")

    def test_record_crud_lifecycle_and_validation(self, api_client):
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.token_alpha}")

        # 1. Validation failure: missing required field 'name'
        res_invalid = api_client.post(
            "/api/v1/o/employee/",
            {"score": 95},
            format="json",
        )
        assert res_invalid.status_code == 400
        assert "Schema validation error" in res_invalid.data["detail"]

        # 2. Valid creation
        res_create = api_client.post(
            "/api/v1/o/employee/",
            {"name": "John Doe", "score": 92, "status": "active"},
            format="json",
        )
        assert res_create.status_code == 201
        rec_id = res_create.data["id"]
        assert res_create.data["data"]["name"] == "John Doe"
        assert res_create.headers["ETag"] == 'W/"1"'

        # 3. Retrieve
        res_get = api_client.get(f"/api/v1/o/employee/{rec_id}/")
        assert res_get.status_code == 200
        assert res_get.data["data"]["score"] == 92

        # 4. Filter with [gte]
        res_filter = api_client.get("/api/v1/o/employee/?data.score[gte]=90")
        assert res_filter.status_code == 200
        assert len(res_filter.data["records"]) == 1

        # 5. Filter with [in]
        res_in = api_client.get("/api/v1/o/employee/?data.status[in]=active,pending")
        assert res_in.status_code == 200
        assert len(res_in.data["records"]) == 1

        # 6. Delete
        res_del = api_client.delete(f"/api/v1/o/employee/{rec_id}/")
        assert res_del.status_code == 204

        # Deleted record is no longer returned
        res_get_del = api_client.get(f"/api/v1/o/employee/{rec_id}/")
        assert res_get_del.status_code == 404

    def test_reference_expansion(self, api_client):
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.token_alpha}")

        # Create department
        res_dept = api_client.post("/api/v1/o/department/", {"name": "Engineering"}, format="json")
        dept_id = res_dept.data["id"]

        # Create employee referencing department
        res_emp = api_client.post(
            "/api/v1/o/employee/",
            {"name": "Alice Engineer", "dept_ref": dept_id},
            format="json",
        )
        emp_id = res_emp.data["id"]

        # Retrieve with ?expand=dept_ref
        res_expanded = api_client.get(f"/api/v1/o/employee/{emp_id}/?expand=dept_ref")
        assert res_expanded.status_code == 200
        assert "dept_ref_expanded" in res_expanded.data["data"]
        assert res_expanded.data["data"]["dept_ref_expanded"]["name"] == "Engineering"

    def test_cross_tenant_record_access_returns_404(self, api_client):
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.token_alpha}")
        res_alpha = api_client.post(
            "/api/v1/o/department/", {"name": "Alpha Secret Dept"}, format="json"
        )
        alpha_dept_id = res_alpha.data["id"]

        # Tenant Beta tries to access Alpha's department
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.token_beta}")
        res_beta = api_client.get(f"/api/v1/o/department/{alpha_dept_id}/")
        # Must return 404 Not Found to prevent enumeration
        assert res_beta.status_code == 404
        assert "not found" in res_beta.data["detail"]
