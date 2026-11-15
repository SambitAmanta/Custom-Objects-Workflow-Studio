import jsonschema
import pytest
from apps.schemas.compat_checker import CompatibilityChecker
from apps.schemas.compiler import compile_object_type_to_json_schema
from apps.schemas.validators import validate_regex_pattern


def test_re2_redos_protection():
    # Valid safe patterns
    validate_regex_pattern(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")
    validate_regex_pattern(r"^\d{4}-\d{2}-\d{2}$")

    # Pattern exceeding 100 characters rejected
    with pytest.raises(ValueError, match="exceeds maximum allowed"):
        validate_regex_pattern("a" * 101)

    # Backreferences (which cause catastrophic backtracking in standard engines) are rejected by re2
    with pytest.raises(ValueError, match="Invalid or unsafe regex"):
        validate_regex_pattern(r"(a+)\1")


def test_json_schema_compiler_all_7_types():
    forge_schema = {
        "slug": "employee",
        "name": "Employee Record",
        "version": 1,
        "fields": [
            {
                "name": "full_name",
                "label": "Full Name",
                "type": "text",
                "required": True,
                "validation_rules": {"min_length": 2, "max_length": 50, "pattern": r"^[A-Za-z ]+$"},
            },
            {
                "name": "salary",
                "label": "Annual Salary",
                "type": "number",
                "required": True,
                "validation_rules": {"minimum": 0, "maximum": 1000000},
            },
            {
                "name": "is_active",
                "label": "Active Status",
                "type": "boolean",
                "default": True,
            },
            {
                "name": "hire_date",
                "label": "Hire Date",
                "type": "date",
                "required": True,
            },
            {
                "name": "department",
                "label": "Department",
                "type": "enum",
                "options": ["Engineering", "Product", "Design", "Sales"],
                "required": True,
            },
            {
                "name": "manager_id",
                "label": "Manager Reference",
                "type": "reference",
                "target_type": "employee",
                "required": False,
            },
            {
                "name": "skills",
                "label": "Skills List",
                "type": "list",
                "item_type": "text",
                "validation_rules": {"min_items": 1},
            },
        ],
    }

    compiled = compile_object_type_to_json_schema(forge_schema)
    assert compiled["$schema"] == "http://json-schema.org/draft-07/schema#"
    assert compiled["type"] == "object"
    assert "full_name" in compiled["properties"]
    assert "salary" in compiled["properties"]
    assert "is_active" in compiled["properties"]
    assert "hire_date" in compiled["properties"]
    assert "department" in compiled["properties"]
    assert "manager_id" in compiled["properties"]
    assert "skills" in compiled["properties"]
    assert compiled["required"] == ["department", "full_name", "hire_date", "salary"]

    # Validate valid document against compiled JSON Schema
    valid_record = {
        "full_name": "Alice Smith",
        "salary": 125000,
        "is_active": True,
        "hire_date": "2026-01-15T09:00:00Z",
        "department": "Engineering",
        "manager_id": "11111111-2222-3333-4444-555555555555",
        "skills": ["Python", "Distributed Systems"],
    }
    jsonschema.validate(instance=valid_record, schema=compiled)

    # Validate invalid document fails schema validation
    invalid_record = dict(valid_record)
    invalid_record["salary"] = -500  # violates minimum: 0
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=invalid_record, schema=compiled)


def test_compatibility_checker_rules():
    base_schema = {
        "slug": "order",
        "fields": [
            {"name": "order_id", "type": "text", "required": True},
            {"name": "status", "type": "enum", "options": ["pending", "shipped"], "required": True},
            {
                "name": "amount",
                "type": "number",
                "validation_rules": {"minimum": 10, "maximum": 100},
            },
        ],
    }

    # Non-breaking changes: add optional field, add enum option, loosen bounds
    non_breaking_schema = {
        "slug": "order",
        "fields": [
            {"name": "order_id", "type": "text", "required": True},
            {
                "name": "status",
                "type": "enum",
                "options": ["pending", "shipped", "delivered"],
                "required": True,
            },
            {
                "name": "amount",
                "type": "number",
                "validation_rules": {"minimum": 5, "maximum": 200},
            },
            {"name": "notes", "type": "text", "required": False},
        ],
    }
    res = CompatibilityChecker.check(base_schema, non_breaking_schema)
    assert res.is_compatible is True
    assert len(res.breaking_changes) == 0
    assert len(res.non_breaking_changes) >= 3

    # Breaking changes: remove field, change type, remove enum option, add required field with no default
    breaking_schema = {
        "slug": "order",
        "fields": [
            {"name": "order_id", "type": "number", "required": True},  # type changed text -> number
            {
                "name": "status",
                "type": "enum",
                "options": ["pending"],
                "required": True,
            },  # removed 'shipped'
            {
                "name": "new_required_no_default",
                "type": "text",
                "required": True,
            },  # required without default
            # 'amount' removed
        ],
    }
    res_breaking = CompatibilityChecker.check(base_schema, breaking_schema)
    assert res_breaking.is_compatible is False
    assert len(res_breaking.breaking_changes) == 4
