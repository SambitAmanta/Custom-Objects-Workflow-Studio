import json
import uuid

import boto3
import httpx
import mongomock
import pytest
from apps.authentication.models import RoleChoices, Tenant
from apps.webhooks.models import WebhookEndpoint
from moto import mock_aws
from workers.consumers.webhook_dispatcher import WebhookDispatcherConsumer


@pytest.mark.django_db
class TestWebhookRetryAndDLQ:
    """Verifies that failing webhook endpoints are retried up to max_attempts

    and routed to the Dead Letter Queue (DLQ) upon exhaustion.
    """

    @pytest.fixture(autouse=True)
    def setup(self, monkeypatch):
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
        monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")

        with mock_aws():
            self.sqs = boto3.client("sqs", region_name="us-east-1")
            dlq = self.sqs.create_queue(QueueName="forge-webhooks-dlq")
            self.dlq_url = dlq["QueueUrl"]

            self.tenant = Tenant.objects.create(name="DLQ Corp", slug="dlq-corp")
            self.mongo_client = mongomock.MongoClient()
            self.db = self.mongo_client["forge_tenant_data"]
            yield

    def test_failing_webhook_retries_and_routes_to_dlq(self):
        # 1. Create Webhook Endpoint
        endpoint = WebhookEndpoint.objects.create(
            tenant=self.tenant,
            url="https://flaky-endpoint.com/webhook",
            role=RoleChoices.VIEWER,
            topics=["records.order.*"],
            is_active=True,
        )

        # 2. Mock HTTP transport to always return HTTP 500 Internal Server Error
        attempt_calls = []

        def mock_500_handler(request: httpx.Request):
            attempt_calls.append(request)
            return httpx.Response(500, json={"error": "Database unavailable"})

        mock_client = httpx.Client(transport=httpx.MockTransport(mock_500_handler))

        consumer = WebhookDispatcherConsumer(
            mongo_client=self.mongo_client,
            db_name="forge_tenant_data",
            sqs_client=self.sqs,
            dlq_url=self.dlq_url,
            http_client=mock_client,
        )

        # Mock validate_and_resolve_url
        consumer.validate_and_resolve_url = lambda url, allow_insecure_http=False: (
            "93.184.216.34",
            "flaky-endpoint.com",
            443,
        )

        event = {
            "event_id": uuid.uuid4().hex,
            "tenant_id": str(self.tenant.id),
            "topic": "records.order.created",
            "action": "created",
            "record_id": uuid.uuid4().hex,
            "version": 1,
        }

        # 3. Dispatch event
        consumer.handle_event(event)

        # 4. Assert 3 retry attempts were made
        assert len(attempt_calls) == 3

        # 5. Assert failed delivery was recorded in MongoDB dead-letter collection
        failed_records = list(
            self.db["failed_webhook_deliveries"].find({"endpoint_id": str(endpoint.id)})
        )
        assert len(failed_records) == 1
        assert failed_records[0]["attempts"] == 3

        # 6. Assert message was routed to SQS DLQ
        dlq_messages = self.sqs.receive_message(
            QueueUrl=self.dlq_url,
            MaxNumberOfMessages=10,
        ).get("Messages", [])
        assert len(dlq_messages) == 1
        body = json.loads(dlq_messages[0]["Body"])
        assert body["event_id"] == event["event_id"]
