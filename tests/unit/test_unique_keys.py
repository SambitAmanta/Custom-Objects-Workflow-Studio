import pytest
from apps.records.services import RecordService, UniqueConstraintError
from apps.schemas.services import SchemaRegistryService
from core.repository import TenantScopedRepository


def test_dynamic_uniqueness_engine(mock_mongo):
    db = mock_mongo["unique_test_db"]
    repo = TenantScopedRepository(tenant_id="tenant_x", db=db)
    schema_service = SchemaRegistryService(repo)
    record_service = RecordService(repo)

    # 1. Define schema with unique email field
    schema_service.save_draft(
        {
            "slug": "customer",
            "name": "Customer",
            "fields": [
                {"name": "email", "type": "text", "required": True, "unique": True},
                {"name": "full_name", "type": "text", "required": True},
            ],
        }
    )
    schema_service.publish_draft("customer")

    # 2. Insert first customer
    rec1 = record_service.create_record(
        "customer", {"email": "john.doe@example.com", "full_name": "John Doe"}
    )
    assert rec1["_id"] is not None

    # 3. Insert duplicate customer with normalized casing/spaces collision -> UniqueConstraintError
    with pytest.raises(UniqueConstraintError) as exc_info:
        record_service.create_record(
            "customer",
            {"email": "  JOHN.DOE@EXAMPLE.COM  ", "full_name": "Imposter John"},
        )
    assert exc_info.value.field_name == "email"

    # 4. PATCH update: update rec1 email to a new address
    rec1_updated = record_service.update_record(
        "customer",
        rec1["_id"],
        {"email": "john.new@example.com"},
        if_match_version=1,
    )
    assert rec1_updated["version"] == 2

    # 5. Now old email "john.doe@example.com" is freed; new record can take it!
    rec2 = record_service.create_record(
        "customer", {"email": "john.doe@example.com", "full_name": "New John"}
    )
    assert rec2["_id"] is not None

    # 6. Soft delete frees rec2 email as well
    record_service.delete_record("customer", rec2["_id"])
    rec3 = record_service.create_record(
        "customer", {"email": "john.doe@example.com", "full_name": "Third John"}
    )
    assert rec3["_id"] is not None
