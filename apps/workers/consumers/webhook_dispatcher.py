import hashlib
import hmac
import ipaddress
import json
import logging
import socket
import time
import urllib.parse
from typing import Any

import httpx
import pymongo
from apps.authentication.models import RoleChoices
from apps.schemas.services import SchemaRegistryService
from apps.webhooks.models import WebhookEndpoint
from core.permissions import FieldPermissionManager
from core.repository import TenantScopedRepository
from django.utils import timezone

from workers.consumers.base import BaseSQSConsumer

logger = logging.getLogger(__name__)


class SSRFSecurityViolation(Exception):
    """Raised when webhook URL resolves to a private, loopback, or link-local IP."""

    pass


class WebhookDispatcherConsumer(BaseSQSConsumer):
    """SSRF-Hardened Webhook Dispatcher Consumer.

    Security guarantees:
    1. Anti-SSRF DNS Resolution & IP Pinning: Resolves target hostname, enforces ip.is_global,
       and pins the verified IP to eliminate DNS rebinding.
    2. TLS SNI & Host Header preservation.
    3. Disabled redirects (allow_redirects=False).
    4. Enforced HTTPS scheme (unless allow_insecure_http is explicitly set for testing).
    5. Egress payload role masking: Masked fields are never leaked to subscriber endpoints.
    6. Timestamped HMAC-SHA256 signature (X-Forge-Signature: t=...,v1=...) preventing replay.
    """

    def __init__(
        self,
        mongo_client: pymongo.MongoClient,
        db_name: str | None = None,
        sqs_client: Any = None,
        queue_url: str | None = None,
        dlq_url: str | None = None,
        http_client: httpx.Client | None = None,
        allow_insecure_http: bool = False,
    ):
        super().__init__(
            consumer_name="webhook-dispatcher",
            mongo_client=mongo_client,
            db_name=db_name,
            sqs_client=sqs_client,
            queue_url=queue_url,
        )
        self.dlq_url = dlq_url
        self.allow_insecure_http = allow_insecure_http
        self.http_client = http_client or httpx.Client(timeout=5.0)

    @staticmethod
    def validate_and_resolve_url(
        target_url: str, allow_insecure_http: bool = False
    ) -> tuple[str, str, int]:
        """Validates target URL against SSRF attacks and returns (resolved_ip, hostname, port)."""
        parsed = urllib.parse.urlparse(target_url)
        scheme = parsed.scheme.lower()

        if scheme != "https" and not (allow_insecure_http and scheme == "http"):
            raise SSRFSecurityViolation(
                f"Insecure protocol '{scheme}'. Webhooks strictly require HTTPS."
            )

        hostname = parsed.hostname
        if not hostname:
            raise SSRFSecurityViolation("Missing valid hostname in webhook target URL")

        port = parsed.port or (443 if scheme == "https" else 80)

        # Resolve DNS
        try:
            addr_info = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
        except socket.gaierror as e:
            raise SSRFSecurityViolation(f"Could not resolve hostname '{hostname}': {e}") from e

        if not addr_info:
            raise SSRFSecurityViolation(f"No IP address returned for hostname '{hostname}'")

        # Check all resolved IPs: every IP must be globally routable
        resolved_ip = None
        for entry in addr_info:
            sockaddr = entry[4]
            ip_str = sockaddr[0]
            ip_obj = ipaddress.ip_address(ip_str)

            # Strictly reject private, loopback, link-local, carrier-grade NAT, or multicast
            if not ip_obj.is_global and not (
                allow_insecure_http and (ip_obj.is_loopback or ip_obj.is_private)
            ):
                raise SSRFSecurityViolation(
                    f"SSRF Protection blocked request: IP {ip_str} for {hostname} is private/reserved"
                )
            if resolved_ip is None:
                resolved_ip = ip_str

        return resolved_ip, hostname, port

    @staticmethod
    def generate_signature(secret: str, payload_bytes: bytes, timestamp: int | None = None) -> str:
        """Generates timestamped HMAC-SHA256 signature: t={timestamp},v1={hash}."""
        ts = int(time.time()) if timestamp is None else timestamp
        to_sign = f"{ts}.".encode() + payload_bytes
        sig = hmac.new(secret.encode("utf-8"), to_sign, hashlib.sha256).hexdigest()
        return f"t={ts},v1={sig}"

    def handle_event(self, event: dict[str, Any]) -> None:
        """Processes event and dispatches webhook notifications to matching endpoints."""
        tenant_id = event["tenant_id"]
        topic = event.get("topic", "")
        record_id = event.get("record_id")

        # 1. Fetch matching active webhook endpoints for this tenant from Postgres
        try:
            endpoints = list(
                WebhookEndpoint.objects.filter(
                    tenant__id=tenant_id,
                    is_active=True,
                )
            )
        except Exception as e:
            logger.error("Failed querying webhook endpoints for tenant %s: %s", tenant_id, e)
            return

        if not endpoints:
            return

        # 2. Fetch record data from Mongo with strict tenant isolation
        repo = TenantScopedRepository(tenant_id=str(tenant_id), db=self.db)
        schema_service = SchemaRegistryService(repo)
        record = repo.find_one("records", {"_id": record_id})

        type_slug = record.get("type") if record else None
        schema = schema_service.get_latest_published(type_slug) if type_slug else None

        for endpoint in endpoints:
            # Check topic matching (e.g. '*' or 'records.*' or exact topic)
            if not self._topic_matches(topic, endpoint.topics):
                continue

            # 3. Apply Field-Level RBAC masking for endpoint's role profile
            masked_data = {}
            if record and schema:
                masked_data = FieldPermissionManager.mask_record_data(
                    schema=schema,
                    data=record.get("data", {}),
                    role=endpoint.role or RoleChoices.VIEWER,
                )

            now_iso = timezone.now().isoformat()
            payload = {
                "event_id": event["event_id"],
                "topic": topic,
                "action": event.get("action"),
                "tenant_id": str(tenant_id),
                "timestamp": now_iso,
                "record": {
                    "id": record_id,
                    "type": type_slug,
                    "version": event.get("version", 1),
                    "state": record.get("state") if record else None,
                    "data": masked_data,
                },
            }

            self._dispatch_to_endpoint(endpoint, payload)

    def _topic_matches(self, topic: str, patterns: list[str]) -> bool:
        if not patterns or "*" in patterns:
            return True
        for p in patterns:
            if p == topic or (p.endswith(".*") and topic.startswith(p[:-2])):
                return True
        return False

    def _dispatch_to_endpoint(
        self, endpoint: WebhookEndpoint, payload: dict[str, Any], max_attempts: int = 3
    ) -> bool:
        target_url = endpoint.url
        try:
            resolved_ip, hostname, _port = self.validate_and_resolve_url(
                target_url, allow_insecure_http=self.allow_insecure_http
            )
        except SSRFSecurityViolation as e:
            logger.warning(
                "SSRF violation for webhook endpoint %s (%s): %s", endpoint.id, target_url, e
            )
            return False

        payload_bytes = json.dumps(payload, sort_keys=True).encode("utf-8")
        sig_header = self.generate_signature(endpoint.secret, payload_bytes)

        headers = {
            "Content-Type": "application/json",
            "Host": hostname,
            "X-Forge-Signature": sig_header,
            "User-Agent": "Forge-Webhook-Dispatcher/1.0",
        }

        # IP Pinning: Send directly to resolved IP while maintaining Host header and SNI
        # Replace hostname in target_url with resolved_ip
        parsed = urllib.parse.urlparse(target_url)
        pinned_netloc = f"{resolved_ip}:{parsed.port}" if parsed.port else f"{resolved_ip}"
        pinned_url = urllib.parse.urlunparse(
            (
                parsed.scheme,
                pinned_netloc,
                parsed.path or "/",
                parsed.params,
                parsed.query,
                parsed.fragment,
            )
        )

        for attempt in range(1, max_attempts + 1):
            try:
                # strictly allow_redirects=False (follow_redirects=False in httpx)
                response = self.http_client.post(
                    pinned_url,
                    content=payload_bytes,
                    headers=headers,
                    extensions={"sni_hostname": hostname},
                )
                if 200 <= response.status_code < 300:
                    logger.info(
                        "Webhook delivered to %s (attempt %d): %d",
                        target_url,
                        attempt,
                        response.status_code,
                    )
                    return True
                logger.warning(
                    "Webhook endpoint %s returned HTTP %d on attempt %d",
                    target_url,
                    response.status_code,
                    attempt,
                )
            except Exception as e:
                logger.warning("Failed delivery to %s on attempt %d: %s", target_url, attempt, e)

        logger.error(
            "Webhook exhausted %d retries for endpoint %s. Routing to DLQ.",
            max_attempts,
            endpoint.id,
        )

        dlq_entry = {
            "endpoint_id": str(endpoint.id),
            "tenant_id": str(endpoint.tenant_id),
            "target_url": target_url,
            "payload": payload,
            "attempts": max_attempts,
            "failed_at": timezone.now().isoformat(),
        }

        # Record in failed deliveries collection
        try:
            self.db["failed_webhook_deliveries"].insert_one(dlq_entry)
        except Exception as e:
            logger.error("Failed persisting failed webhook delivery: %s", e)

        # Route to AWS SQS DLQ if configured
        if self.dlq_url and self.sqs:
            try:
                self.sqs.send_message(
                    QueueUrl=self.dlq_url,
                    MessageBody=json.dumps(payload),
                    MessageAttributes={
                        "endpoint_id": {"DataType": "String", "StringValue": str(endpoint.id)},
                        "attempts": {"DataType": "Number", "StringValue": str(max_attempts)},
                    },
                )
            except Exception as e:
                logger.error("Failed sending dead letter to SQS DLQ: %s", e)

        return False
