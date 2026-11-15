import base64
import json
import uuid
from typing import Any

import jsonschema
from apps.schemas.compiler import compile_object_type_to_json_schema
from apps.schemas.services import SchemaRegistryService
from core.repository import TenantScopedRepository
from core.unit_of_work import UnitOfWork
from django.utils import timezone
from pymongo.errors import DuplicateKeyError


class OptimisticLockError(Exception):
    """Raised when an If-Match ETag version does not match the current record version."""

    pass


class UniqueConstraintError(Exception):
    """Raised when a unique field value collision occurs."""

    def __init__(self, field_name: str, message: str = None):
        self.field_name = field_name
        super().__init__(message or f"Value for unique field '{field_name}' already exists")


class RecordService:
    def __init__(self, repo: TenantScopedRepository):
        self.repo = repo
        self.uow = UnitOfWork(repo)
        self.schema_service = SchemaRegistryService(repo)

    def _get_active_schema(self, type_slug: str) -> dict[str, Any]:
        schema = self.schema_service.get_latest_published(type_slug)
        if not schema:
            raise ValueError(f"No published schema found for type '{type_slug}'")
        return schema

    def create_record(
        self, type_slug: str, data: dict[str, Any], principal_id: str = "anonymous"
    ) -> dict[str, Any]:
        schema = self._get_active_schema(type_slug)
        compiled_schema = compile_object_type_to_json_schema(schema)

        # 1. Validate data against compiled JSON Schema
        try:
            jsonschema.validate(instance=data, schema=compiled_schema)
        except jsonschema.ValidationError as e:
            raise ValueError(f"Schema validation error: {e.message}") from e

        # 2. Extract unique fields and initial workflow state
        fields = schema.get("fields", [])
        unique_fields = [f for f in fields if f.get("unique", False)]
        initial_state = schema.get("workflow", {}).get("initial_state", "draft")

        record_id = uuid.uuid4().hex
        event_id = uuid.uuid4().hex
        now = timezone.now().isoformat()

        def _transaction_work(session):
            # A. Insert into unique_keys
            for uf in unique_fields:
                field_name = uf["name"]
                if field_name in data and data[field_name] is not None:
                    val_hash = UnitOfWork.hash_unique_value(data[field_name])
                    try:
                        self.repo.insert_one(
                            UnitOfWork.UNIQUE_KEYS_COLLECTION,
                            {
                                "tenant_id": self.repo.tenant_id,
                                "type": type_slug,
                                "field": field_name,
                                "value_hash": val_hash,
                                "record_id": record_id,
                            },
                            session=session,
                        )
                    except DuplicateKeyError as exc:
                        raise UniqueConstraintError(field_name) from exc

            # B. Insert into records
            record_doc = {
                "_id": record_id,
                "tenant_id": self.repo.tenant_id,
                "type": type_slug,
                "schema_version": schema.get("version", 1),
                "data": data,
                "state": initial_state,
                "version": 1,
                "created_at": now,
                "updated_at": now,
                "deleted_at": None,
            }
            self.repo.insert_one(UnitOfWork.RECORDS_COLLECTION, record_doc, session=session)

            # C. In-transaction immutable audit entry
            self.repo.insert_one(
                UnitOfWork.AUDIT_COLLECTION,
                {
                    "event_id": event_id,
                    "tenant_id": self.repo.tenant_id,
                    "type": type_slug,
                    "record_id": record_id,
                    "version": 1,
                    "action": "created",
                    "principal_id": principal_id,
                    "changed_fields": list(data.keys()),
                    "timestamp": now,
                },
                session=session,
            )

            # D. Thin Outbox Event
            self.repo.insert_one(
                UnitOfWork.OUTBOX_COLLECTION,
                {
                    "event_id": event_id,
                    "tenant_id": self.repo.tenant_id,
                    "topic": f"records.{type_slug}.created",
                    "action": "created",
                    "record_id": record_id,
                    "version": 1,
                    "changed_fields": list(data.keys()),
                    "tombstone": None,
                    "status": "pending",
                    "locked_until": None,
                    "created_at": now,
                },
                session=session,
            )
            return record_doc

        return self.uow.run_transaction(_transaction_work)

    def get_record(
        self, type_slug: str, record_id: str, expand: list[str] = None
    ) -> dict[str, Any] | None:
        record = self.repo.find_one(
            UnitOfWork.RECORDS_COLLECTION,
            {"_id": record_id, "type": type_slug},
        )
        if not record:
            return None

        # Handle ?expand=<ref_field>
        if expand and "data" in record:
            schema = self.schema_service.get_latest_published(type_slug)
            if schema:
                ref_fields = {
                    f["name"]: f for f in schema.get("fields", []) if f.get("type") == "reference"
                }
                for field_name in expand:
                    if field_name in ref_fields and field_name in record["data"]:
                        target_id = record["data"][field_name]
                        target_type = ref_fields[field_name].get("target_type")
                        if target_id and target_type:
                            target_doc = self.repo.find_one(
                                UnitOfWork.RECORDS_COLLECTION,
                                {"_id": target_id, "type": target_type},
                            )
                            if target_doc:
                                record["data"][f"{field_name}_expanded"] = target_doc.get("data")
        return record

    def update_record(
        self,
        type_slug: str,
        record_id: str,
        patch_data: dict[str, Any],
        if_match_version: int,
        principal_id: str = "anonymous",
    ) -> dict[str, Any]:
        existing = self.repo.find_one(
            UnitOfWork.RECORDS_COLLECTION,
            {"_id": record_id, "type": type_slug},
        )
        if not existing:
            return None

        # Verify Optimistic Concurrency (ETag)
        if existing.get("version") != if_match_version:
            raise OptimisticLockError(
                f"Version mismatch: client requested version {if_match_version}, current version is {existing.get('version')}"
            )

        schema = self._get_active_schema(type_slug)
        compiled_schema = compile_object_type_to_json_schema(schema)

        # Merge patch in-memory and validate merged document
        merged_data = {**existing.get("data", {}), **patch_data}
        try:
            jsonschema.validate(instance=merged_data, schema=compiled_schema)
        except jsonschema.ValidationError as e:
            raise ValueError(f"Schema validation error on patch: {e.message}") from e

        fields = schema.get("fields", [])
        unique_fields = {f["name"]: f for f in fields if f.get("unique", False)}
        new_version = existing.get("version", 1) + 1
        event_id = uuid.uuid4().hex
        now = timezone.now().isoformat()

        def _transaction_work(session):
            # A. Update unique_keys if any unique field changed
            for field_name in patch_data:
                if field_name in unique_fields:
                    old_val = existing.get("data", {}).get(field_name)
                    new_val = patch_data[field_name]
                    if old_val != new_val:
                        if old_val is not None:
                            old_hash = UnitOfWork.hash_unique_value(old_val)
                            self.repo.delete_one(
                                UnitOfWork.UNIQUE_KEYS_COLLECTION,
                                {
                                    "tenant_id": self.repo.tenant_id,
                                    "type": type_slug,
                                    "field": field_name,
                                    "value_hash": old_hash,
                                },
                                session=session,
                            )
                        if new_val is not None:
                            new_hash = UnitOfWork.hash_unique_value(new_val)
                            try:
                                self.repo.insert_one(
                                    UnitOfWork.UNIQUE_KEYS_COLLECTION,
                                    {
                                        "tenant_id": self.repo.tenant_id,
                                        "type": type_slug,
                                        "field": field_name,
                                        "value_hash": new_hash,
                                        "record_id": record_id,
                                    },
                                    session=session,
                                )
                            except DuplicateKeyError as exc:
                                raise UniqueConstraintError(field_name) from exc

            # B. Atomically update record using dotted paths
            update_ops = {f"data.{k}": v for k, v in patch_data.items()}
            update_ops["version"] = new_version
            update_ops["updated_at"] = now

            updated = self.repo.find_one_and_update(
                UnitOfWork.RECORDS_COLLECTION,
                {"_id": record_id, "type": type_slug, "version": if_match_version},
                {"$set": update_ops},
                session=session,
                return_document=True,
            )
            if not updated:
                raise OptimisticLockError("Concurrent update detected; lost race")

            # C. In-transaction audit entry
            self.repo.insert_one(
                UnitOfWork.AUDIT_COLLECTION,
                {
                    "event_id": event_id,
                    "tenant_id": self.repo.tenant_id,
                    "type": type_slug,
                    "record_id": record_id,
                    "version": new_version,
                    "action": "updated",
                    "principal_id": principal_id,
                    "changed_fields": list(patch_data.keys()),
                    "timestamp": now,
                },
                session=session,
            )

            # D. Thin Outbox Event
            self.repo.insert_one(
                UnitOfWork.OUTBOX_COLLECTION,
                {
                    "event_id": event_id,
                    "tenant_id": self.repo.tenant_id,
                    "topic": f"records.{type_slug}.updated",
                    "action": "updated",
                    "record_id": record_id,
                    "version": new_version,
                    "changed_fields": list(patch_data.keys()),
                    "tombstone": None,
                    "status": "pending",
                    "locked_until": None,
                    "created_at": now,
                },
                session=session,
            )
            return updated

        return self.uow.run_transaction(_transaction_work)

    def delete_record(
        self,
        type_slug: str,
        record_id: str,
        if_match_version: int | None = None,
        principal_id: str = "anonymous",
    ) -> bool:
        existing = self.repo.find_one(
            UnitOfWork.RECORDS_COLLECTION,
            {"_id": record_id, "type": type_slug},
        )
        if not existing:
            return False

        if if_match_version is not None and existing.get("version") != if_match_version:
            raise OptimisticLockError(
                f"Version mismatch: expected {if_match_version}, current is {existing.get('version')}"
            )

        event_id = uuid.uuid4().hex
        now = timezone.now().isoformat()

        def _transaction_work(session):
            # A. Free all unique_keys associated with this record
            self.repo.delete_one(
                UnitOfWork.UNIQUE_KEYS_COLLECTION,
                {"tenant_id": self.repo.tenant_id, "record_id": record_id},
                session=session,
            )

            # B. Soft-delete the record
            self.repo.soft_delete(
                UnitOfWork.RECORDS_COLLECTION,
                {"_id": record_id, "type": type_slug},
                session=session,
            )

            # C. In-transaction audit entry
            self.repo.insert_one(
                UnitOfWork.AUDIT_COLLECTION,
                {
                    "event_id": event_id,
                    "tenant_id": self.repo.tenant_id,
                    "type": type_slug,
                    "record_id": record_id,
                    "version": existing.get("version", 1),
                    "action": "deleted",
                    "principal_id": principal_id,
                    "changed_fields": [],
                    "timestamp": now,
                },
                session=session,
            )

            # D. Thin Outbox Event with Tombstone
            self.repo.insert_one(
                UnitOfWork.OUTBOX_COLLECTION,
                {
                    "event_id": event_id,
                    "tenant_id": self.repo.tenant_id,
                    "topic": f"records.{type_slug}.deleted",
                    "action": "deleted",
                    "record_id": record_id,
                    "version": existing.get("version", 1),
                    "changed_fields": [],
                    "tombstone": {
                        "record_id": record_id,
                        "type": type_slug,
                        "deleted_at": now,
                    },
                    "status": "pending",
                    "locked_until": None,
                    "created_at": now,
                },
                session=session,
            )
            return True

        return self.uow.run_transaction(_transaction_work)

    def list_records(
        self,
        type_slug: str,
        filters: dict[str, Any],
        sort_field: str = "_id",
        sort_dir: int = 1,
        cursor: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Lists records with whitelisted filtering and composite cursor pagination."""
        schema = self._get_active_schema(type_slug)
        declared_fields = {f["name"]: f for f in schema.get("fields", [])}

        mongo_filter: dict[str, Any] = {
            "type": type_slug,
            "tenant_id": self.repo.tenant_id,
            "deleted_at": None,
        }

        # Whitelist filtering
        for key, val in filters.items():
            if key.startswith("data."):
                raw_name = key[5:]
                op = "eq"
                if "[" in raw_name and raw_name.endswith("]"):
                    parts = raw_name.split("[", 1)
                    field_name = parts[0]
                    op = parts[1][:-1]
                else:
                    field_name = raw_name

                if field_name in declared_fields:
                    f_type = declared_fields[field_name].get("type")
                    dot_path = f"data.{field_name}"

                    def _parse_val(v):
                        if f_type == "number":
                            try:
                                return int(v) if str(v).isdigit() or str(v).lstrip("-").isdigit() else float(v)
                            except (ValueError, TypeError):
                                return v
                        elif f_type == "boolean":
                            return str(v).lower() in ("true", "1")
                        return v

                    if op == "eq":
                        mongo_filter[dot_path] = _parse_val(val)
                    elif op == "gte":
                        mongo_filter[dot_path] = {"$gte": _parse_val(val)}
                    elif op == "lte":
                        mongo_filter[dot_path] = {"$lte": _parse_val(val)}
                    elif op == "in":
                        mongo_filter[dot_path] = {"$in": [_parse_val(v.strip()) for v in str(val).split(",") if v.strip()]}

        # Handle Composite Cursor Pagination (sort_val, _id)
        if cursor:
            try:
                decoded = json.loads(
                    base64.urlsafe_b64decode(cursor.encode("utf-8")).decode("utf-8")
                )
                cursor_sort_val = decoded["sort_val"]
                cursor_id = decoded["id"]
                if sort_dir == 1:
                    mongo_filter["$or"] = [
                        {sort_field: {"$gt": cursor_sort_val}},
                        {sort_field: cursor_sort_val, "_id": {"$gt": cursor_id}},
                    ]
                else:
                    mongo_filter["$or"] = [
                        {sort_field: {"$lt": cursor_sort_val}},
                        {sort_field: cursor_sort_val, "_id": {"$lt": cursor_id}},
                    ]
            except Exception:
                pass  # invalid cursor ignored

        query_cursor = (
            self.repo.db[UnitOfWork.RECORDS_COLLECTION]
            .find(mongo_filter)
            .sort([(sort_field, sort_dir), ("_id", sort_dir)])
            .limit(limit + 1)
        )
        items = list(query_cursor)

        has_next = len(items) > limit
        if has_next:
            items = items[:limit]
            last = items[-1]
            last_sort_val = (
                last.get(sort_field)
                if not sort_field.startswith("data.")
                else last.get("data", {}).get(sort_field[5:])
            )
            next_cursor_payload = {"sort_val": last_sort_val, "id": str(last["_id"])}
            next_cursor = base64.urlsafe_b64encode(
                json.dumps(next_cursor_payload).encode("utf-8")
            ).decode("utf-8")
        else:
            next_cursor = None

        return {
            "records": items,
            "next_cursor": next_cursor,
            "has_next": has_next,
        }
