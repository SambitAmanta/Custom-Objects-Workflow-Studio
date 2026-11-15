import concurrent.futures
import datetime

import jwt
import pytest
from apps.authentication.models import RoleChoices, Tenant, TenantMembership, User
from core.repository import TenantScopedRepository
from django.conf import settings
from rest_framework.test import APIClient


@pytest.mark.django_db(transaction=True)
class TestWorkflowGuardsAndRaces:
    @pytest.fixture(autouse=True)
    def setup_data(self, api_client):
        self.tenant = Tenant.objects.create(name="Workflow Corp", slug="workflow-corp")
        self.admin = User.objects.create_user(email="admin@corp.com", password="password")
        self.editor = User.objects.create_user(email="editor@corp.com", password="password")
        self.viewer = User.objects.create_user(email="viewer@corp.com", password="password")

        TenantMembership.objects.create(
            user=self.admin, tenant=self.tenant, role=RoleChoices.ADMIN, is_default=True
        )
        TenantMembership.objects.create(
            user=self.editor, tenant=self.tenant, role=RoleChoices.EDITOR, is_default=True
        )
        TenantMembership.objects.create(
            user=self.viewer, tenant=self.tenant, role=RoleChoices.VIEWER, is_default=True
        )

        self.admin_token = self._token_for(self.admin, RoleChoices.ADMIN)
        self.editor_token = self._token_for(self.editor, RoleChoices.EDITOR)
        self.viewer_token = self._token_for(self.viewer, RoleChoices.VIEWER)

        # Publish invoice object type with workflow rules
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.admin_token}")
        api_client.post(
            "/api/v1/schemas/types/",
            {
                "slug": "invoice",
                "name": "Invoice",
                "fields": [
                    {"name": "title", "type": "text", "required": True},
                    {"name": "amount", "type": "number", "required": True},
                ],
                "workflow": {
                    "initial_state": "draft",
                    "states": ["draft", "submitted", "approved", "rejected"],
                    "transitions": [
                        {
                            "name": "submit",
                            "from_state": "draft",
                            "to_state": "submitted",
                            "roles": ["editor", "admin"],
                            "conditions": {"<=": [{"var": "data.amount"}, 1000]},
                        },
                        {
                            "name": "approve",
                            "from_state": "submitted",
                            "to_state": "approved",
                            "roles": ["admin"],
                            "conditions": None,
                        },
                        {
                            "name": "reject",
                            "from_state": "submitted",
                            "to_state": "rejected",
                            "roles": ["admin"],
                            "conditions": None,
                        },
                    ],
                },
            },
            format="json",
        )
        api_client.post("/api/v1/schemas/types/invoice/publish/")

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

    def test_viewer_forbidden_from_submitting(self, mock_mongo):
        admin_client = APIClient()
        admin_client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.admin_token}")
        res = admin_client.post(
            "/api/v1/o/invoice/",
            {"title": "Small expense", "amount": 250},
            format="json",
        )
        assert res.status_code == 201
        record_id = res.data["id"]

        viewer_client = APIClient()
        viewer_client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.viewer_token}")
        transition_res = viewer_client.post(
            f"/api/v1/o/invoice/{record_id}/transitions/submit/",
        )
        assert transition_res.status_code == 403
        assert "not authorized" in transition_res.data["detail"]

    def test_condition_guard_rejects_amount_over_limit(self, mock_mongo):
        editor_client = APIClient()
        editor_client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.editor_token}")
        res = editor_client.post(
            "/api/v1/o/invoice/",
            {"title": "Big budget expense", "amount": 1500},
            format="json",
        )
        assert res.status_code == 201
        record_id = res.data["id"]

        # Attempt to submit invoice with amount 1500 (> 1000 threshold)
        transition_res = editor_client.post(
            f"/api/v1/o/invoice/{record_id}/transitions/submit/",
        )
        assert transition_res.status_code == 400
        assert "condition guard not satisfied" in transition_res.data["detail"]

    def test_valid_transition_and_audit_outbox_emission(self, mock_mongo):
        editor_client = APIClient()
        editor_client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.editor_token}")
        res = editor_client.post(
            "/api/v1/o/invoice/",
            {"title": "Valid expense", "amount": 750},
            format="json",
        )
        assert res.status_code == 201
        record_id = res.data["id"]

        transition_res = editor_client.post(
            f"/api/v1/o/invoice/{record_id}/transitions/submit/",
        )
        assert transition_res.status_code == 200
        assert transition_res.data["state"] == "submitted"
        assert transition_res.data["version"] == 2
        assert transition_res["ETag"] == 'W/"2"'

        # Calling submit again should return 409 Conflict (invalid from_state)
        second_res = editor_client.post(
            f"/api/v1/o/invoice/{record_id}/transitions/submit/",
        )
        assert second_res.status_code == 409

        # Verify audit record and outbox row
        repo = TenantScopedRepository(tenant_id=str(self.tenant.id))
        audit_records = list(repo.find("audit_events", {"record_id": record_id}))
        assert len(audit_records) == 2  # 1 created + 1 transitioned
        transition_audit = [a for a in audit_records if a["action"] == "transitioned"][0]
        assert transition_audit["transition"] == "submit"
        assert transition_audit["from_state"] == "draft"
        assert transition_audit["to_state"] == "submitted"

        outbox_events = list(repo.find("outbox", {"record_id": record_id}))
        assert len(outbox_events) == 2  # 1 created + 1 transitioned
        transition_outbox = [e for e in outbox_events if e["action"] == "transitioned"][0]
        assert transition_outbox["topic"] == "records.invoice.transitioned"
        assert transition_outbox["transition"] == "submit"
        assert transition_outbox["status"] == "pending"

    def test_available_transitions_endpoint(self, mock_mongo):
        admin_client = APIClient()
        admin_client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.admin_token}")
        res = admin_client.post(
            "/api/v1/o/invoice/",
            {"title": "Office Supplies", "amount": 300},
            format="json",
        )
        record_id = res.data["id"]

        # Viewer sees 0 available transitions
        viewer_client = APIClient()
        viewer_client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.viewer_token}")
        v_res = viewer_client.get(f"/api/v1/o/invoice/{record_id}/transitions/")
        assert v_res.status_code == 200
        assert len(v_res.data["available_transitions"]) == 0

        # Editor sees 'submit'
        editor_client = APIClient()
        editor_client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.editor_token}")
        e_res = editor_client.get(f"/api/v1/o/invoice/{record_id}/transitions/")
        assert e_res.status_code == 200
        assert len(e_res.data["available_transitions"]) == 1
        assert e_res.data["available_transitions"][0]["name"] == "submit"

    def test_concurrent_approve_and_reject_race(self, mock_mongo):
        admin_client = APIClient()
        admin_client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.admin_token}")
        res = admin_client.post(
            "/api/v1/o/invoice/",
            {"title": "Capital Purchase", "amount": 800},
            format="json",
        )
        assert res.status_code == 201
        record_id = res.data["id"]

        # Transition to 'submitted'
        submit_res = admin_client.post(f"/api/v1/o/invoice/{record_id}/transitions/submit/")
        assert submit_res.status_code == 200

        # Concurrently race 'approve' vs 'reject'
        def call_transition(name: str):
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.admin_token}")
            return client.post(f"/api/v1/o/invoice/{record_id}/transitions/{name}/")

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            future_approve = executor.submit(call_transition, "approve")
            future_reject = executor.submit(call_transition, "reject")
            res_approve = future_approve.result()
            res_reject = future_reject.result()

        status_codes = [res_approve.status_code, res_reject.status_code]
        assert 200 in status_codes, f"Expected one 200, got: {status_codes}"
        assert 409 in status_codes, f"Expected one 409 Conflict, got: {status_codes}"

        # Verify final record state in MongoDB
        repo = TenantScopedRepository(tenant_id=str(self.tenant.id))
        final_record = repo.find_one("records", {"_id": record_id})
        assert final_record["state"] in ("approved", "rejected")
        assert final_record["version"] == 3

        # Exactly 2 transition audit logs (submit + winning transition)
        audit_transitions = list(
            repo.find("audit_events", {"record_id": record_id, "action": "transitioned"})
        )
        assert len(audit_transitions) == 2

        # Exactly 2 transition outbox events (submit + winning transition)
        outbox_transitions = list(
            repo.find("outbox", {"record_id": record_id, "action": "transitioned"})
        )
        assert len(outbox_transitions) == 2
