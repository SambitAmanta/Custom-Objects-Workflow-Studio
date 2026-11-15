import datetime
import json
import logging
import uuid
from typing import Any

import boto3
import pymongo
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


class OutboxRelayWorker:
    """Outbox Relay Worker that polls the MongoDB outbox collection with atomic lease claims

    and publishes thin events to AWS Standard SQS.

    Design details:
    1. Claim query: {$or: [{status: "pending"}, {status: "in_flight", locked_until: {$lt: now}}]}
       Prevents stranding claimed rows if a worker dies mid-batch.
    2. Atomically claims records with find_one_and_update to prevent duplicate concurrent publishing.
    3. Emits thin events containing event_id, tenant_id, topic, action, record_id, and version.
    4. Has failure injection points for Chaos Testing (zero lost events & zero duplicate effects).
    """

    def __init__(
        self,
        mongo_client: pymongo.MongoClient,
        db_name: str | None = None,
        sqs_client: Any = None,
        queue_url: str | None = None,
        worker_id: str | None = None,
        lease_seconds: int = 30,
    ):
        self.client = mongo_client
        self.db = mongo_client[db_name or getattr(settings, "MONGO_DB_NAME", "forge_tenant_data")]
        self.collection = self.db["outbox"]
        self.queue_url = queue_url or getattr(settings, "SQS_OUTBOX_QUEUE_URL", "")
        self.worker_id = worker_id or f"relay-{uuid.uuid4().hex[:8]}"
        self.lease_seconds = lease_seconds

        if sqs_client is not None:
            self.sqs = sqs_client
        else:
            kwargs = {
                "region_name": getattr(settings, "AWS_REGION", "us-east-1"),
                "aws_access_key_id": getattr(settings, "AWS_ACCESS_KEY_ID", "test"),
                "aws_secret_access_key": getattr(settings, "AWS_SECRET_ACCESS_KEY", "test"),
            }
            endpoint_url = getattr(settings, "AWS_ENDPOINT_URL", None)
            if endpoint_url:
                kwargs["endpoint_url"] = endpoint_url
            self.sqs = boto3.client("sqs", **kwargs)

        self._ensure_indexes()

    def _ensure_indexes(self):
        try:
            self.collection.create_index(
                [
                    ("status", pymongo.ASCENDING),
                    ("locked_until", pymongo.ASCENDING),
                    ("created_at", pymongo.ASCENDING),
                ]
            )
            # TTL index to automatically purge published events after 7 days
            self.collection.create_index(
                "published_at",
                expireAfterSeconds=7 * 24 * 3600,
            )
        except Exception as e:
            logger.debug(f"Outbox index creation note: {e}")

    def claim_batch(self, batch_size: int = 50) -> list[dict[str, Any]]:
        """Claims a batch of pending or expired in_flight outbox events atomically."""
        now = timezone.now()
        now_iso = now.isoformat()
        lease_expiration = (now + datetime.timedelta(seconds=self.lease_seconds)).isoformat()

        # Resilient claim filter:
        # Match either pending status, OR in_flight status whose lease has expired
        claim_filter = {
            "$or": [
                {"status": "pending"},
                {"status": "in_flight", "locked_until": {"$lt": now_iso}},
            ]
        }

        candidates = list(
            self.collection.find(claim_filter)
            .sort("created_at", pymongo.ASCENDING)
            .limit(batch_size)
        )

        claimed = []
        for candidate in candidates:
            updated = self.collection.find_one_and_update(
                {
                    "_id": candidate["_id"],
                    "$or": [
                        {"status": "pending"},
                        {"status": "in_flight", "locked_until": {"$lt": now_iso}},
                    ],
                },
                {
                    "$set": {
                        "status": "in_flight",
                        "locked_until": lease_expiration,
                        "locked_by": self.worker_id,
                    }
                },
                return_document=pymongo.ReturnDocument.AFTER,
            )
            if updated:
                claimed.append(updated)

        return claimed

    def publish_event(
        self,
        event: dict[str, Any],
        fail_before_sqs: bool = False,
        fail_after_sqs: bool = False,
    ) -> str:
        """Publishes single event to SQS and marks published in outbox atomically."""
        event_id = event["event_id"]
        tenant_id = event["tenant_id"]
        topic = event.get("topic", "unknown")
        version = event.get("version", 1)

        payload = {
            "event_id": event_id,
            "tenant_id": tenant_id,
            "topic": topic,
            "action": event.get("action"),
            "record_id": event.get("record_id"),
            "version": version,
            "transition": event.get("transition"),
            "changed_fields": event.get("changed_fields", []),
            "tombstone": event.get("tombstone"),
            "created_at": event.get("created_at"),
        }

        # 1. Chaos injection hook: crash before SQS publish
        if fail_before_sqs:
            raise RuntimeError(f"Simulated crash BEFORE publishing event {event_id} to SQS")

        # 2. Publish to standard SQS
        response = self.sqs.send_message(
            QueueUrl=self.queue_url,
            MessageBody=json.dumps(payload),
            MessageAttributes={
                "tenant_id": {"DataType": "String", "StringValue": str(tenant_id)},
                "topic": {"DataType": "String", "StringValue": str(topic)},
                "version": {"DataType": "Number", "StringValue": str(version)},
            },
        )
        sqs_message_id = response.get("MessageId")

        # 3. Chaos injection hook: crash after SQS publish before outbox update
        if fail_after_sqs:
            raise RuntimeError(f"Simulated crash AFTER publishing event {event_id} to SQS")

        # 4. Mark published in outbox
        now = timezone.now().isoformat()
        self.collection.update_one(
            {"_id": event["_id"]},
            {
                "$set": {
                    "status": "published",
                    "published_at": now,
                    "sqs_message_id": sqs_message_id,
                }
            },
        )
        return sqs_message_id

    def process_batch(
        self,
        batch_size: int = 50,
        fail_before_sqs: bool = False,
        fail_after_sqs: bool = False,
    ) -> int:
        claimed = self.claim_batch(batch_size=batch_size)
        published_count = 0
        for event in claimed:
            self.publish_event(
                event,
                fail_before_sqs=fail_before_sqs,
                fail_after_sqs=fail_after_sqs,
            )
            published_count += 1
        return published_count
