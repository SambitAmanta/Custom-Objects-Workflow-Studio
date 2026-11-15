import datetime
import hashlib
import json
from typing import Any

import pymongo
from core.repository import TenantScopedRepository
from django.utils import timezone


class IdempotencyPayloadMismatchError(Exception):
    """Raised when an idempotency key is re-used with a different request payload (HTTP 422)."""

    pass


class IdempotencyManager:
    COLLECTION = "idempotency_keys"

    def __init__(self, repo: TenantScopedRepository):
        self.repo = repo
        self._ensure_indexes()

    def _ensure_indexes(self):
        try:
            # 1. Unique index scoped by (tenant, principal, key)
            self.repo.db[self.COLLECTION].create_index(
                [
                    ("tenant_id", pymongo.ASCENDING),
                    ("principal_id", pymongo.ASCENDING),
                    ("key", pymongo.ASCENDING),
                ],
                unique=True,
            )
            # 2. TTL index to automatically purge expired keys after 24 hours
            self.repo.db[self.COLLECTION].create_index(
                [("expires_at", pymongo.ASCENDING)],
                expireAfterSeconds=0,
            )
        except Exception:
            pass

    @staticmethod
    def compute_request_hash(method: str, path: str, body: Any) -> str:
        """Computes deterministic SHA-256 hash over (method, path, body)."""
        if isinstance(body, (dict, list)):
            body_str = json.dumps(body, sort_keys=True)
        elif isinstance(body, bytes):
            body_str = body.decode("utf-8", errors="replace")
        elif body is None:
            body_str = ""
        else:
            body_str = str(body)

        payload_to_hash = f"{method.upper()}:{path}:{body_str}".encode()
        return hashlib.sha256(payload_to_hash).hexdigest()

    def get_cached_response(
        self,
        principal_id: str,
        key: str,
        request_hash: str,
    ) -> dict[str, Any] | None:
        """Checks for existing idempotency key.
        If found and hash matches, returns cached response.
        If found and hash differs, raises IdempotencyPayloadMismatchError (HTTP 422).
        If not found, returns None.
        """
        doc = self.repo.db[self.COLLECTION].find_one(
            {
                "tenant_id": self.repo.tenant_id,
                "principal_id": str(principal_id),
                "key": str(key),
            }
        )
        if not doc:
            return None

        if doc.get("request_hash") != request_hash:
            raise IdempotencyPayloadMismatchError(
                "Idempotency-Key was previously used with a different request body or path (RFC 7231)"
            )

        return {
            "status_code": doc.get("status_code", 200),
            "headers": doc.get("response_headers", {}),
            "body": doc.get("response_body"),
        }

    def save_response(
        self,
        principal_id: str,
        key: str,
        request_hash: str,
        status_code: int,
        response_headers: dict[str, str],
        response_body: dict[str, Any],
        session=None,
    ) -> None:
        """Stores successful 2xx response against the idempotency key with a 24-hour TTL."""
        # Only cache successful 2xx responses
        if not (200 <= status_code < 300):
            return

        now = timezone.now()
        expires_at = now + datetime.timedelta(hours=24)

        doc = {
            "tenant_id": self.repo.tenant_id,
            "principal_id": str(principal_id),
            "key": str(key),
            "request_hash": request_hash,
            "status_code": status_code,
            "response_headers": response_headers,
            "response_body": response_body,
            "created_at": now.isoformat(),
            "expires_at": expires_at,
        }
        self.repo.db[self.COLLECTION].insert_one(doc, session=session)
