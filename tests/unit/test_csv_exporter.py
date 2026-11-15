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
