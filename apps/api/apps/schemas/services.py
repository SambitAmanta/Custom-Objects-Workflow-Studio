from typing import Any

import pymongo
from apps.schemas.compat_checker import CompatibilityChecker
from apps.schemas.compiler import compile_object_type_to_json_schema
from apps.schemas.validators import VALID_FIELD_TYPES, validate_regex_pattern
from core.repository import TenantScopedRepository
from django.utils import timezone


class SchemaRegistryService:
    COLLECTION = "object_types"

    def __init__(self, repo: TenantScopedRepository):
        self.repo = repo
        self._ensure_indexes()

    def _ensure_indexes(self):
        """Ensures compound unique index on { tenant_id: 1, slug: 1, version: 1 }."""
        try:
            self.repo.db[self.COLLECTION].create_index(
                [
                    ("tenant_id", pymongo.ASCENDING),
                    ("slug", pymongo.ASCENDING),
                    ("version", pymongo.ASCENDING),
                ],
                unique=True,
            )
        except Exception:
            pass  # Index already exists or mongomock

    def get_draft(self, slug: str) -> dict[str, Any] | None:
        return self.repo.find_one(self.COLLECTION, {"slug": slug, "status": "draft"})

    def get_latest_published(self, slug: str) -> dict[str, Any] | None:
        cursor = (
            self.repo.find(
                self.COLLECTION,
                {"slug": slug, "status": "published"},
            )
            .sort("version", pymongo.DESCENDING)
            .limit(1)
        )
        results = list(cursor)
        return results[0] if results else None

    def get_version(self, slug: str, version: int) -> dict[str, Any] | None:
        return self.repo.find_one(
            self.COLLECTION,
            {"slug": slug, "version": int(version), "status": "published"},
        )

    def list_versions(self, slug: str) -> list[dict[str, Any]]:
        cursor = self.repo.find(
            self.COLLECTION,
            {"slug": slug, "status": "published"},
        ).sort("version", pymongo.DESCENDING)
        return list(cursor)

    def save_draft(self, schema_data: dict[str, Any]) -> dict[str, Any]:
        """Validates and saves a draft schema definition."""
        slug = schema_data.get("slug")
        if not slug or not isinstance(slug, str):
            raise ValueError("Schema must include a valid string slug")

        # Validate fields
        fields = schema_data.get("fields", [])
        field_names = set()
        for f in fields:
            name = f.get("name")
            f_type = f.get("type")
            if not name or name in field_names:
                raise ValueError(f"Duplicate or invalid field name: {name}")
            field_names.add(name)

            if f_type not in VALID_FIELD_TYPES:
                raise ValueError(f"Invalid field type '{f_type}' for field '{name}'")

            rules = f.get("validation_rules", {}) or {}
            if "pattern" in rules and rules["pattern"]:
                validate_regex_pattern(rules["pattern"])

        now = timezone.now().isoformat()
        draft_doc = {
            "tenant_id": self.repo.tenant_id,
            "slug": slug,
            "name": schema_data.get("name", slug.title()),
            "description": schema_data.get("description", ""),
            "version": 0,  # Drafts are version 0
            "status": "draft",
            "fields": fields,
            "workflow": schema_data.get(
                "workflow", {"initial_state": "draft", "states": ["draft"], "transitions": []}
            ),
            "permissions": schema_data.get("permissions", {}),
            "updated_at": now,
        }

        existing = self.get_draft(slug)
        if existing:
            self.repo.update_one(
                self.COLLECTION,
                {"slug": slug, "status": "draft"},
                {"$set": draft_doc},
            )
        else:
            draft_doc["created_at"] = now
            self.repo.insert_one(self.COLLECTION, draft_doc)

        return self.get_draft(slug)

    def publish_draft(self, slug: str, allow_breaking: bool = False) -> dict[str, Any]:
        """Freezes draft schema into an immutable published version."""
        draft = self.get_draft(slug)
        if not draft:
            raise ValueError(f"No draft schema found for '{slug}'")

        latest = self.get_latest_published(slug)
        next_version = 1
        if latest:
            next_version = latest.get("version", 1) + 1
            # Check compatibility
            compat = CompatibilityChecker.check(latest, draft)
            if not compat.is_compatible and not allow_breaking:
                raise ValueError(
                    f"Draft contains breaking changes requiring migration: {compat.breaking_changes}"
                )

        now = timezone.now().isoformat()
        published_doc = {
            "tenant_id": self.repo.tenant_id,
            "slug": slug,
            "name": draft.get("name", slug.title()),
            "description": draft.get("description", ""),
            "version": next_version,
            "status": "published",
            "fields": draft.get("fields", []),
            "workflow": draft.get("workflow", {}),
            "permissions": draft.get("permissions", {}),
            "created_at": now,
            "updated_at": now,
        }

        # Insert immutable version
        self.repo.insert_one(self.COLLECTION, published_doc)

        # Remove draft
        self.repo.delete_one(self.COLLECTION, {"slug": slug, "status": "draft"})

        # Pre-compile JSON Schema to verify compilation
        compile_object_type_to_json_schema(published_doc)

        return self.get_version(slug, next_version)
