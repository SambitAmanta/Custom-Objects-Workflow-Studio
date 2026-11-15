import pytest
from core.repository import TenantScopedRepository


def test_repository_requires_tenant_id(mock_mongo):
    db = mock_mongo["test_db"]
    with pytest.raises(ValueError, match="requires an authenticated non-empty tenant_id"):
        TenantScopedRepository(tenant_id="", db=db)
    with pytest.raises(ValueError, match="requires an authenticated non-empty tenant_id"):
        TenantScopedRepository(tenant_id=None, db=db)


def test_insert_one_enforces_tenant_id(mock_mongo):
    db = mock_mongo["test_db"]
    repo = TenantScopedRepository(tenant_id="tenant_alpha", db=db)

    result = repo.insert_one("records", {"name": "Test Record"})
    assert result.inserted_id is not None

    raw_doc = db["records"].find_one({"_id": result.inserted_id})
    assert raw_doc["tenant_id"] == "tenant_alpha"
    assert raw_doc["deleted_at"] is None
    assert "created_at" in raw_doc
    assert "updated_at" in raw_doc


def test_insert_one_rejects_mismatched_tenant_id(mock_mongo):
    db = mock_mongo["test_db"]
    repo = TenantScopedRepository(tenant_id="tenant_alpha", db=db)

    with pytest.raises(ValueError, match="Mismatched tenant_id"):
        repo.insert_one("records", {"name": "Sneaky", "tenant_id": "tenant_beta"})


def test_find_one_strict_tenant_isolation(mock_mongo):
    db = mock_mongo["test_db"]
    repo_alpha = TenantScopedRepository(tenant_id="tenant_alpha", db=db)
    repo_beta = TenantScopedRepository(tenant_id="tenant_beta", db=db)

    res_alpha = repo_alpha.insert_one("records", {"title": "Alpha Secret"})

    # Alpha can find its own document
    found_alpha = repo_alpha.find_one("records", {"_id": res_alpha.inserted_id})
    assert found_alpha is not None
    assert found_alpha["title"] == "Alpha Secret"

    # Beta querying by the exact same ID returns None (zero leakage)
    found_beta = repo_beta.find_one("records", {"_id": res_alpha.inserted_id})
    assert found_beta is None


def test_find_excludes_soft_deleted(mock_mongo):
    db = mock_mongo["test_db"]
    repo = TenantScopedRepository(tenant_id="tenant_alpha", db=db)

    res = repo.insert_one("records", {"title": "To be deleted"})
    record_id = res.inserted_id

    # Active find
    assert repo.find_one("records", {"_id": record_id}) is not None

    # Soft delete
    repo.soft_delete("records", {"_id": record_id})

    # Standard find_one returns None
    assert repo.find_one("records", {"_id": record_id}) is None

    # find_with_deleted returns the document
    deleted_doc = repo.find_with_deleted("records", {"_id": record_id})
    assert deleted_doc is not None
    assert deleted_doc["deleted_at"] is not None


def test_aggregate_injects_tenant_match(mock_mongo):
    db = mock_mongo["test_db"]
    repo_alpha = TenantScopedRepository(tenant_id="tenant_alpha", db=db)
    repo_beta = TenantScopedRepository(tenant_id="tenant_beta", db=db)

    repo_alpha.insert_one("orders", {"amount": 100})
    repo_alpha.insert_one("orders", {"amount": 200})
    repo_beta.insert_one("orders", {"amount": 999})

    # Aggregate total amount for alpha
    pipeline = [{"$group": {"_id": None, "total": {"$sum": "$amount"}}}]
    results = list(repo_alpha.aggregate("orders", pipeline))
    assert len(results) == 1
    assert results[0]["total"] == 300  # Excludes beta's 999!
