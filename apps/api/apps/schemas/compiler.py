from functools import lru_cache
from typing import Any

from apps.schemas.validators import VALID_FIELD_TYPES, validate_regex_pattern


def compile_field_to_json_schema(field: dict[str, Any]) -> dict[str, Any]:
    """Compiles an individual Forge field definition into a JSON Schema property descriptor."""
    field_type = field.get("type")
    if field_type not in VALID_FIELD_TYPES:
        raise ValueError(
            f"Unsupported field type: {field_type}. Must be one of {VALID_FIELD_TYPES}"
        )

    rules = field.get("validation_rules", {}) or {}
    prop: dict[str, Any] = {
        "title": field.get("label", field.get("name")),
    }

    if "default" in field and field["default"] is not None:
        prop["default"] = field["default"]

    if field_type == "text":
        prop["type"] = "string"
        if "min_length" in rules:
            prop["minLength"] = int(rules["min_length"])
        if "max_length" in rules:
            prop["maxLength"] = int(rules["max_length"])
        if "pattern" in rules and rules["pattern"]:
            validate_regex_pattern(rules["pattern"])
            prop["pattern"] = rules["pattern"]

    elif field_type == "number":
        prop["type"] = ["number", "integer"]
        if "minimum" in rules:
            prop["minimum"] = float(rules["minimum"])
        if "maximum" in rules:
            prop["maximum"] = float(rules["maximum"])

    elif field_type == "boolean":
        prop["type"] = "boolean"

    elif field_type == "date":
        prop["type"] = "string"
        prop["format"] = "date-time"

    elif field_type == "enum":
        options = field.get("options") or rules.get("options") or []
        if not options or not isinstance(options, list):
            raise ValueError(
                f"Enum field '{field.get('name')}' must specify a non-empty list of options"
            )
        prop["type"] = "string"
        prop["enum"] = [str(opt) for opt in options]

    elif field_type == "reference":
        target_type = field.get("target_type")
        if not target_type:
            raise ValueError(f"Reference field '{field.get('name')}' must specify target_type")
        prop["type"] = "string"
        prop["format"] = "uuid"
        prop["description"] = f"Reference to {target_type} record ID"

    elif field_type == "list":
        item_type = field.get("item_type", "text")
        if item_type not in ("text", "number", "reference", "enum"):
            raise ValueError(f"Unsupported list item_type: {item_type}")

        inner_field = {
            "name": f"{field.get('name')}_item",
            "type": item_type,
            "options": field.get("options", []),
            "target_type": field.get("target_type"),
        }
        prop["type"] = "array"
        prop["items"] = compile_field_to_json_schema(inner_field)
        if "min_items" in rules:
            prop["minItems"] = int(rules["min_items"])
        if "max_items" in rules:
            prop["maxItems"] = int(rules["max_items"])

    return prop


def compile_object_type_to_json_schema(schema_doc: dict[str, Any]) -> dict[str, Any]:
    """Compiles a complete Forge object type definition into a Draft-07 JSON Schema."""
    slug = schema_doc.get("slug")
    version = schema_doc.get("version", 1)
    fields = schema_doc.get("fields", [])

    properties: dict[str, Any] = {}
    required_fields: list[str] = []

    for f in fields:
        name = f.get("name")
        if not name or not isinstance(name, str):
            raise ValueError("Every field descriptor must have a valid string name")
        properties[name] = compile_field_to_json_schema(f)
        if f.get("required", False):
            required_fields.append(name)

    compiled: dict[str, Any] = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": schema_doc.get("name", slug),
        "description": f"Generated schema for {slug} (version {version})",
        "type": "object",
        "properties": properties,
        "required": sorted(required_fields),
        "additionalProperties": False,
    }
    return compiled


@lru_cache(maxsize=1024)
def get_compiled_schema_cache(
    tenant_id: str, slug: str, version: int, schema_hash: str
) -> dict[str, Any]:
    """Cache compiled schema in-process. Since published schemas are immutable,
    the cache key includes schema_hash for safety.
    """
    pass  # Used as an in-process LRU cache holder
