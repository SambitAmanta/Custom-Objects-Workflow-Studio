import csv
import io
import logging
from typing import Any

import boto3
import pymongo
from core.repository import TenantScopedRepository
from django.conf import settings

from workers.consumers.base import BaseSQSConsumer

logger = logging.getLogger(__name__)


class CSVStreamingExporter(BaseSQSConsumer):
    """Streams tenant object records matching an export request into AWS S3 with deterministic keys."""

    def __init__(
        self,
        mongo_client: pymongo.MongoClient,
        db_name: str | None = None,
        sqs_client: Any = None,
        s3_client: Any = None,
        queue_url: str | None = None,
        bucket_name: str | None = None,
    ):
        super().__init__(
            consumer_name="csv-streaming-exporter",
            mongo_client=mongo_client,
            db_name=db_name,
            sqs_client=sqs_client,
            queue_url=queue_url,
        )
        self.bucket_name = bucket_name or getattr(
            settings, "S3_EXPORT_BUCKET_NAME", "forge-exports"
        )

        if s3_client is not None:
            self.s3 = s3_client
        else:
            kwargs = {
                "region_name": getattr(settings, "AWS_REGION", "us-east-1"),
                "aws_access_key_id": getattr(settings, "AWS_ACCESS_KEY_ID", "test"),
                "aws_secret_access_key": getattr(settings, "AWS_SECRET_ACCESS_KEY", "test"),
            }
            endpoint_url = getattr(settings, "AWS_ENDPOINT_URL", None)
            if endpoint_url:
                kwargs["endpoint_url"] = endpoint_url
            self.s3 = boto3.client("s3", **kwargs)

    def handle_event(self, event: dict[str, Any]) -> None:
        """Processes an export.requested event and streams CSV to S3."""
        if event.get("topic") != "export.requested":
            return

        tenant_id = event["tenant_id"]
        type_slug = event.get("type_slug")
        export_id = event.get("export_id") or event.get("event_id")

        if not type_slug or not export_id:
            logger.warning("Missing type_slug or export_id in export event: %s", event)
            return

        self.export_records_to_s3(tenant_id=tenant_id, type_slug=type_slug, export_id=export_id)

    def export_records_to_s3(self, tenant_id: str, type_slug: str, export_id: str) -> str:
        """Streams tenant records to S3 with deterministic key: tenants/{tenant_id}/exports/{export_id}.csv"""
        repo = TenantScopedRepository(tenant_id=str(tenant_id), db=self.db)
        records_cursor = repo.find(
            "records",
            {"type": type_slug, "tombstone": None},
        ).sort("_id", pymongo.ASCENDING)

        s3_key = f"tenants/{tenant_id}/exports/{export_id}.csv"

        # Stream rows to memory buffer and upload to S3
        buffer = io.StringIO()
        fieldnames = ["id", "type", "version", "state", "created_at", "updated_at"]
        data_keys = set()

        records_list = list(records_cursor)
        for r in records_list:
            data_keys.update(r.get("data", {}).keys())

        all_headers = fieldnames + sorted(data_keys)
        writer = csv.DictWriter(buffer, fieldnames=all_headers)
        writer.writeheader()

        for r in records_list:
            row = {
                "id": str(r.get("_id")),
                "type": r.get("type"),
                "version": r.get("version"),
                "state": r.get("state"),
                "created_at": r.get("created_at"),
                "updated_at": r.get("updated_at"),
            }
            for k, v in r.get("data", {}).items():
                row[k] = v
            writer.writerow(row)

        csv_bytes = buffer.getvalue().encode("utf-8")
        self.s3.put_object(
            Bucket=self.bucket_name,
            Key=s3_key,
            Body=csv_bytes,
            ContentType="text/csv",
        )
        logger.info(
            "Exported %d records for tenant %s type %s to s3://%s/%s",
            len(records_list),
            tenant_id,
            type_slug,
            self.bucket_name,
            s3_key,
        )
        return s3_key
