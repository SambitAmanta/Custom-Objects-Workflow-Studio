import json
import logging
from abc import ABC, abstractmethod
from typing import Any

import boto3
import pymongo
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


class BaseSQSConsumer(ABC):
    """Abstract Base SQS Consumer featuring an idempotent deduplication ledger

    to guarantee zero duplicate effects across asynchronous consumers.
    """

    LEDGER_COLLECTION = "processed_events"

    def __init__(
        self,
        consumer_name: str,
        mongo_client: pymongo.MongoClient,
        db_name: str | None = None,
        sqs_client: Any = None,
        queue_url: str | None = None,
    ):
        self.consumer_name = consumer_name
        self.client = mongo_client
        self.db = mongo_client[db_name or getattr(settings, "MONGO_DB_NAME", "forge_tenant_data")]
        self.ledger = self.db[self.LEDGER_COLLECTION]
        self.queue_url = queue_url or getattr(settings, "SQS_OUTBOX_QUEUE_URL", "")

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
            self.ledger.create_index(
                [
                    ("tenant_id", pymongo.ASCENDING),
                    ("event_id", pymongo.ASCENDING),
                    ("consumer_name", pymongo.ASCENDING),
                ],
                unique=True,
            )
        except Exception as e:
            logger.debug(f"Ledger index creation note: {e}")

    def claim_event_deduplication(self, tenant_id: str, event_id: str, version: int = 1) -> bool:
        """Atomically inserts into deduplication ledger.

        Returns True if this consumer claimed processing rights (first time seen),
        or False if it is a duplicate delivery (already processed or currently claimed).
        """
        now = timezone.now().isoformat()
        try:
            self.ledger.insert_one(
                {
                    "tenant_id": tenant_id,
                    "event_id": event_id,
                    "consumer_name": self.consumer_name,
                    "version": version,
                    "processed_at": now,
                }
            )
            return True
        except pymongo.errors.DuplicateKeyError:
            logger.info(
                "Consumer %s: duplicate event %s detected for tenant %s. Skipping duplicate effect.",
                self.consumer_name,
                event_id,
                tenant_id,
            )
            return False

    @abstractmethod
    def handle_event(self, event: dict[str, Any]) -> None:
        """Subclass implementation of domain event processing."""
        pass

    def process_messages(self, max_messages: int = 10, wait_time_seconds: int = 1) -> int:
        """Polls SQS queue, deduplicates using the MongoDB ledger, and executes handle_event."""
        response = self.sqs.receive_message(
            QueueUrl=self.queue_url,
            MaxNumberOfMessages=max_messages,
            WaitTimeSeconds=wait_time_seconds,
            MessageAttributeNames=["All"],
        )

        messages = response.get("Messages", [])
        processed_count = 0

        for msg in messages:
            receipt_handle = msg["ReceiptHandle"]
            try:
                event = json.loads(msg["Body"])
                event_id = event.get("event_id")
                tenant_id = event.get("tenant_id")
                version = event.get("version", 1)

                if not event_id or not tenant_id:
                    logger.warning("Malformed event payload without event_id or tenant_id: %s", msg)
                    self.sqs.delete_message(QueueUrl=self.queue_url, ReceiptHandle=receipt_handle)
                    continue

                # Deduplication Gate
                is_first_delivery = self.claim_event_deduplication(
                    tenant_id=tenant_id, event_id=event_id, version=version
                )

                if is_first_delivery:
                    self.handle_event(event)

                # Acknowledge / delete message from SQS
                self.sqs.delete_message(QueueUrl=self.queue_url, ReceiptHandle=receipt_handle)
                processed_count += 1
            except Exception as e:
                logger.exception("Error processing SQS message %s: %s", msg.get("MessageId"), e)
                # Do not delete message so it can be retried or moved to DLQ

        return processed_count
