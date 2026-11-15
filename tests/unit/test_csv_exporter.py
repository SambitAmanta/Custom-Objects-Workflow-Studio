import csv
import io
import uuid

import boto3
import mongomock
import pytest
from moto import mock_aws
from workers.consumers.csv_exporter import CSVStreamingExporter


@pytest.mark.django_db
class TestCSVStreamingExporter:
    @pytest.fixture(autouse=True)
    def setup(self, monkeypatch):
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
        monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
        monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
        monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")

        with mock_aws():
            self.s3 = boto3.client("s3", region_name="us-east-1")
            self.bucket_name = "test-forge-exports"
            self.s3.create_bucket(Bucket=self.bucket_name)

            self.mongo_client = mongomock.MongoClient()
            self.tenant_id = str(uuid.uuid4())
            self.exporter = CSVStreamingExporter(
                mongo_client=self.mongo_client,
                db_name="forge_tenant_data",
                s3_client=self.s3,
                bucket_name=self.bucket_name,
            )
            yield

    def test_export_records_to_s3_deterministic_key(self):
        # 1. Insert records into mongo
        db = self.mongo_client["forge_tenant_data"]
        for i in range(3):
            db["records"].insert_one(
                {
                    "_id": f"rec-{i}",
                    "tenant_id": self.tenant_id,
                    "type": "contact",
                    "version": 1,
                    "state": "active",
                    "data": {"name": f"Person {i}", "email": f"p{i}@corp.com"},
                    "created_at": "2026-11-15T12:00:00Z",
                    "updated_at": "2026-11-15T12:00:00Z",
                    "tombstone": None,
                }
            )

        export_id = "exp-999"
        key = self.exporter.export_records_to_s3(
            tenant_id=self.tenant_id, type_slug="contact", export_id=export_id
        )

        assert key == f"tenants/{self.tenant_id}/exports/{export_id}.csv"

        # 2. Verify S3 file contents
        s3_obj = self.s3.get_object(Bucket=self.bucket_name, Key=key)
        csv_text = s3_obj["Body"].read().decode("utf-8")
        reader = list(csv.DictReader(io.StringIO(csv_text)))

        assert len(reader) == 3
        assert reader[0]["id"] == "rec-0"
        assert reader[0]["name"] == "Person 0"
        assert reader[0]["email"] == "p0@corp.com"

    def test_export_records_to_s3_masks_fields_for_viewer_and_editor(self):
        """Verifies that CSV streaming export masks sensitive fields according to role."""
        db = self.mongo_client["forge_tenant_data"]

        # Insert schema with field permissions
        db["object_types"].insert_one(
            {
                "tenant_id": self.tenant_id,
                "slug": "employee",
                "version": 1,
                "status": "published",
                "fields": [
                    {"name": "full_name", "type": "text"},
                    {"name": "salary", "type": "number"},
                    {"name": "ssn", "type": "text"},
                ],
                "permissions": {
                    "field_permissions": {
                        "ssn": {"admin": "write", "editor": "masked", "viewer": "hidden"},
                        "salary": {"admin": "write", "editor": "read", "viewer": "hidden"},
                    }
                },
            }
        )

        db["records"].insert_one(
            {
                "_id": "emp-1",
                "tenant_id": self.tenant_id,
                "type": "employee",
                "version": 1,
                "state": "active",
                "data": {
                    "full_name": "John Doe",
                    "salary": 120000,
                    "ssn": "123-45-6789",
                },
                "created_at": "2026-11-15T12:00:00Z",
                "updated_at": "2026-11-15T12:00:00Z",
                "tombstone": None,
            }
        )

        # 1. Export as Viewer: ssn and salary must be completely hidden
        viewer_key = self.exporter.export_records_to_s3(
            tenant_id=self.tenant_id,
            type_slug="employee",
            export_id="exp-viewer-1",
            role="viewer",
        )
        viewer_csv = (
            self.s3.get_object(Bucket=self.bucket_name, Key=viewer_key)["Body"]
            .read()
            .decode("utf-8")
        )
        viewer_rows = list(csv.DictReader(io.StringIO(viewer_csv)))
        assert "ssn" not in viewer_rows[0]
        assert "salary" not in viewer_rows[0]
        assert viewer_rows[0]["full_name"] == "John Doe"

        # 2. Export as Editor: ssn must be masked (***MASKED***)
        editor_key = self.exporter.export_records_to_s3(
            tenant_id=self.tenant_id,
            type_slug="employee",
            export_id="exp-editor-1",
            role="editor",
        )
        editor_csv = (
            self.s3.get_object(Bucket=self.bucket_name, Key=editor_key)["Body"]
            .read()
            .decode("utf-8")
        )
        editor_rows = list(csv.DictReader(io.StringIO(editor_csv)))
        assert editor_rows[0]["ssn"] == "***MASKED***"
        assert editor_rows[0]["salary"] == "120000"

    def test_audit_log_omits_sensitive_payload_data(self):
        """Verifies that audit log entries preserve metadata without storing raw sensitive field values."""
        from apps.records.services import RecordService
        from core.repository import TenantScopedRepository

        db = self.mongo_client["forge_tenant_data"]
        db["object_types"].insert_one(
            {
                "tenant_id": self.tenant_id,
                "slug": "confidential_doc",
                "version": 1,
                "status": "published",
                "fields": [
                    {"name": "doc_id", "type": "text"},
                    {"name": "secret_code", "type": "text"},
                ],
            }
        )

        repo = TenantScopedRepository(tenant_id=self.tenant_id, db=db)
        service = RecordService(repo)

        service.create_record(
            type_slug="confidential_doc",
            data={"doc_id": "DOC-001", "secret_code": "HIGHLY-CLASSIFIED-9988"},
            principal_id="admin@corp.com",
            role="admin",
        )

        audit_entry = db["audit_events"].find_one(
            {"tenant_id": self.tenant_id, "type": "confidential_doc"}
        )
        assert audit_entry is not None
        assert audit_entry["action"] == "created"
        assert audit_entry["principal_id"] == "admin@corp.com"
        assert "changed_fields" in audit_entry
        assert "secret_code" in audit_entry["changed_fields"]
        # Raw secret value MUST NOT be present in audit entry
        assert "HIGHLY-CLASSIFIED-9988" not in str(audit_entry)
