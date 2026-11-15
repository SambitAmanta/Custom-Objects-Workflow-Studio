import datetime
import uuid

import boto3
import mongomock
import pytest
from moto import mock_aws
from workers.consumers.base import BaseSQSConsumer
from workers.relay.publisher import OutboxRelayWorker


class CountingTestConsumer(BaseSQSConsumer):
    def __init__(self, *args, **kwargs):
        super().__init__(consumer_name="test-counter-consumer", *args, **kwargs)
        self.execution_count = 0
        self.processed_events = []

    def handle_event(self, event):
        self.execution_count += 1
        self.processed_events.append(event)


@pytest.mark.django_db
class TestRelayWorkerCrashAndDeduplication:
    """Chaos Test #5: Verify that relay worker crashes before and after publishing
    guarantee Zero Lost Events and Zero Duplicate Effects on downstream consumers.
    """

    @pytest.fixture(autouse=True)
    def setup_sqs(self, monkeypatch):
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
        monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
        monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
        monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")

        with mock_aws():
            self.sqs = boto3.client(
                "sqs",
                region_name="us-east-1",
            )
            queue = self.sqs.create_queue(QueueName="forge-chaos-outbox")
            self.queue_url = queue["QueueUrl"]
            self.mongo_client = mongomock.MongoClient()
            self.db = self.mongo_client["forge_test_db"]
            self.tenant_id = str(uuid.uuid4())
            yield

    def test_relay_crash_before_sqs_publish_zero_lost_events(self):
        """Scenario A: Worker claims event, then crashes before SQS publish.

        Event lease expires and a subsequent worker successfully claims and delivers it.
        """
        event_id = uuid.uuid4().hex
        now = datetime.datetime.now(datetime.UTC)

        # 1. Insert pending event into outbox
        self.db["outbox"].insert_one(
            {
                "event_id": event_id,
                "tenant_id": self.tenant_id,
                "topic": "records.order.created",
                "action": "created",
                "record_id": uuid.uuid4().hex,
                "version": 1,
                "status": "pending",
                "locked_until": None,
                "locked_by": None,
                "created_at": (now - datetime.timedelta(seconds=10)).isoformat(),
            }
        )

        # 2. First relay worker attempts to publish but crashes BEFORE SQS send
        worker_1 = OutboxRelayWorker(
            mongo_client=self.mongo_client,
            db_name="forge_test_db",
            sqs_client=self.sqs,
            queue_url=self.queue_url,
            worker_id="relay-1",
            lease_seconds=5,
        )

        with pytest.raises(RuntimeError, match="Simulated crash BEFORE"):
            worker_1.process_batch(batch_size=1, fail_before_sqs=True)

        # 3. Assert event was claimed and left in_flight
        doc = self.db["outbox"].find_one({"event_id": event_id})
        assert doc["status"] == "in_flight"
        assert doc["locked_by"] == "relay-1"

        # 4. Fast forward time past lease expiration
        expired_time = (now - datetime.timedelta(seconds=1)).isoformat()
        self.db["outbox"].update_one(
            {"event_id": event_id}, {"$set": {"locked_until": expired_time}}
        )

        # 5. Second relay worker restarts and claims expired event
        worker_2 = OutboxRelayWorker(
            mongo_client=self.mongo_client,
            db_name="forge_test_db",
            sqs_client=self.sqs,
            queue_url=self.queue_url,
            worker_id="relay-2",
            lease_seconds=30,
        )

        published_count = worker_2.process_batch(batch_size=10)
        assert published_count == 1

        # 6. Verify outbox state is now 'published'
        doc_after = self.db["outbox"].find_one({"event_id": event_id})
        assert doc_after["status"] == "published"
        assert doc_after["sqs_message_id"] is not None

        # 7. Verify SQS received exactly 1 message
        messages = self.sqs.receive_message(
            QueueUrl=self.queue_url,
            MaxNumberOfMessages=10,
        ).get("Messages", [])
        assert len(messages) == 1

    def test_relay_crash_after_sqs_publish_zero_duplicate_effects(self):
        """Scenario B: Worker publishes to SQS, then crashes before updating MongoDB outbox status.

        Worker restarts after lease, republishes to SQS (creating duplicate SQS message).
        Downstream consumer deduplication ledger catches the duplicate and prevents double execution.
        """
        event_id = uuid.uuid4().hex
        now = datetime.datetime.now(datetime.UTC)

        self.db["outbox"].insert_one(
            {
                "event_id": event_id,
                "tenant_id": self.tenant_id,
                "topic": "records.invoice.approved",
                "action": "transitioned",
                "record_id": uuid.uuid4().hex,
                "version": 2,
                "status": "pending",
                "locked_until": None,
                "locked_by": None,
                "created_at": (now - datetime.timedelta(seconds=10)).isoformat(),
            }
        )

        worker_1 = OutboxRelayWorker(
            mongo_client=self.mongo_client,
            db_name="forge_test_db",
            sqs_client=self.sqs,
            queue_url=self.queue_url,
            worker_id="relay-1",
            lease_seconds=5,
        )

        # Crash after publishing to SQS
        with pytest.raises(RuntimeError, match="Simulated crash AFTER"):
            worker_1.process_batch(batch_size=1, fail_after_sqs=True)

        # The message is in SQS, but outbox status is still 'in_flight'
        doc = self.db["outbox"].find_one({"event_id": event_id})
        assert doc["status"] == "in_flight"

        # Expire lease to simulate recovery after crash
        expired_time = (now - datetime.timedelta(seconds=1)).isoformat()
        self.db["outbox"].update_one(
            {"event_id": event_id}, {"$set": {"locked_until": expired_time}}
        )

        # Worker 2 restarts and republishes the same event
        worker_2 = OutboxRelayWorker(
            mongo_client=self.mongo_client,
            db_name="forge_test_db",
            sqs_client=self.sqs,
            queue_url=self.queue_url,
            worker_id="relay-2",
        )
        worker_2.process_batch(batch_size=10)

        # Outbox marked published
        doc_after = self.db["outbox"].find_one({"event_id": event_id})
        assert doc_after["status"] == "published"

        # SQS now has 2 duplicate messages for this event_id
        consumer = CountingTestConsumer(
            mongo_client=self.mongo_client,
            db_name="forge_test_db",
            sqs_client=self.sqs,
            queue_url=self.queue_url,
        )

        # Process first message
        count_1 = consumer.process_messages(max_messages=1, wait_time_seconds=0)
        assert count_1 == 1
        assert consumer.execution_count == 1

        # Process second duplicate message
        count_2 = consumer.process_messages(max_messages=1, wait_time_seconds=0)
        assert count_2 == 1
        # Crucial Invariant: Execution count MUST STILL BE 1 (zero duplicate side effects)
        assert consumer.execution_count == 1, (
            "Duplicate delivery executed business logic more than once!"
        )
