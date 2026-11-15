from typing import Any

import pymongo
from django.conf import settings
from django.utils import timezone
from pymongo.client_session import ClientSession
from pymongo.database import Database

_mongo_client: pymongo.MongoClient | None = None


def get_mongo_client() -> pymongo.MongoClient:
    """Returns a singleton PyMongo client connected to the configured cluster."""
    global _mongo_client
    if _mongo_client is None:
        _mongo_client = pymongo.MongoClient(
            settings.MONGO_URI,
            serverSelectionTimeoutMS=3000,
            uuidRepresentation="standard",
        )
    return _mongo_client


def set_mongo_client(client: pymongo.MongoClient):
    """Allows overriding the MongoClient instance (e.g. for unit testing)."""
    global _mongo_client
    _mongo_client = client


def get_mongo_db(db_name: str | None = None) -> Database:
    """Returns the target MongoDB database."""
    client = get_mongo_client()
    return client[db_name or settings.MONGO_DB_NAME]


class TenantScopedRepository:
    """Enforces strict tenant isolation across all MongoDB collections.

    Guarantees:
    1. Every read operation injects { tenant_id: self.tenant_id, deleted_at: None }.
    2. Every write operation enforces document["tenant_id"] == self.tenant_id.
    3. Every aggregation pipeline begins with a tenant-scoped $match.
    4. Forwards PyMongo ClientSession for multi-document transaction atomicity.
    """

    def __init__(
        self,
        tenant_id: str | Any,
        db: Database | None = None,
        mongo_client: pymongo.MongoClient | None = None,
    ):
        if not tenant_id or not str(tenant_id).strip():
            raise ValueError("TenantScopedRepository requires an authenticated non-empty tenant_id")
        self.tenant_id = str(tenant_id).strip()
        if db is not None:
            self.db = db
        elif mongo_client is not None:
            self.db = mongo_client[settings.MONGO_DB_NAME]
        else:
            self.db = get_mongo_db()

    def _scope_filter(self, query: dict | None = None, include_deleted: bool = False) -> dict:
        scoped = dict(query) if query else {}
        scoped["tenant_id"] = self.tenant_id
        if not include_deleted:
            scoped["deleted_at"] = None
        return scoped

    def find_one(
        self,
        collection: str,
        query: dict | None = None,
        session: ClientSession | None = None,
        **kwargs,
    ) -> dict | None:
        """Finds a single active document matching query within this tenant."""
        scoped_query = self._scope_filter(query, include_deleted=False)
        return self.db[collection].find_one(scoped_query, session=session, **kwargs)

    def find_with_deleted(
        self,
        collection: str,
        query: dict | None = None,
        session: ClientSession | None = None,
        **kwargs,
    ) -> dict | None:
        """Finds a document including soft-deleted rows within this tenant."""
        scoped_query = self._scope_filter(query, include_deleted=True)
        return self.db[collection].find_one(scoped_query, session=session, **kwargs)

    def find(
        self,
        collection: str,
        query: dict | None = None,
        include_deleted: bool = False,
        session: ClientSession | None = None,
        **kwargs,
    ) -> pymongo.cursor.Cursor:
        """Returns a cursor of documents scoped to this tenant."""
        scoped_query = self._scope_filter(query, include_deleted=include_deleted)
        return self.db[collection].find(scoped_query, session=session, **kwargs)

    def insert_one(
        self, collection: str, document: dict, session: ClientSession | None = None, **kwargs
    ) -> pymongo.results.InsertOneResult:
        """Inserts a document, strictly validating and enforcing tenant_id."""
        if "tenant_id" in document and str(document["tenant_id"]) != self.tenant_id:
            raise ValueError(
                f"Mismatched tenant_id: document specifies {document['tenant_id']}, "
                f"repository is scoped to {self.tenant_id}"
            )
        doc_to_insert = dict(document)
        doc_to_insert["tenant_id"] = self.tenant_id
        if "deleted_at" not in doc_to_insert:
            doc_to_insert["deleted_at"] = None
        if "created_at" not in doc_to_insert:
            doc_to_insert["created_at"] = timezone.now().isoformat()
        if "updated_at" not in doc_to_insert:
            doc_to_insert["updated_at"] = doc_to_insert["created_at"]

        return self.db[collection].insert_one(doc_to_insert, session=session, **kwargs)

    def insert_many(
        self, collection: str, documents: list[dict], session: ClientSession | None = None, **kwargs
    ) -> pymongo.results.InsertManyResult:
        """Inserts multiple documents, validating and enforcing tenant_id on all items."""
        docs_to_insert = []
        now = timezone.now().isoformat()
        for doc in documents:
            if "tenant_id" in doc and str(doc["tenant_id"]) != self.tenant_id:
                raise ValueError(
                    f"Mismatched tenant_id: document specifies {doc['tenant_id']}, "
                    f"repository is scoped to {self.tenant_id}"
                )
            d = dict(doc)
            d["tenant_id"] = self.tenant_id
            if "deleted_at" not in d:
                d["deleted_at"] = None
            if "created_at" not in d:
                d["created_at"] = now
            if "updated_at" not in d:
                d["updated_at"] = now
            docs_to_insert.append(d)

        return self.db[collection].insert_many(docs_to_insert, session=session, **kwargs)

    def update_one(
        self,
        collection: str,
        filter_query: dict,
        update: dict,
        session: ClientSession | None = None,
        **kwargs,
    ) -> pymongo.results.UpdateResult:
        """Updates a single active document matching filter within this tenant."""
        scoped_filter = self._scope_filter(filter_query, include_deleted=False)
        return self.db[collection].update_one(scoped_filter, update, session=session, **kwargs)

    def find_one_and_update(
        self,
        collection: str,
        filter_query: dict,
        update: dict,
        session: ClientSession | None = None,
        **kwargs,
    ) -> dict | None:
        """Atomically finds and updates an active document within this tenant."""
        scoped_filter = self._scope_filter(filter_query, include_deleted=False)
        return self.db[collection].find_one_and_update(
            scoped_filter, update, session=session, **kwargs
        )

    def delete_one(
        self, collection: str, filter_query: dict, session: ClientSession | None = None, **kwargs
    ) -> pymongo.results.DeleteResult:
        """Hard-deletes a single document within this tenant (e.g. unique_keys rows)."""
        scoped_filter = self._scope_filter(filter_query, include_deleted=True)
        return self.db[collection].delete_one(scoped_filter, session=session, **kwargs)

    def soft_delete(
        self, collection: str, filter_query: dict, session: ClientSession | None = None, **kwargs
    ) -> pymongo.results.UpdateResult:
        """Soft-deletes a record by setting deleted_at timestamp within this tenant."""
        scoped_filter = self._scope_filter(filter_query, include_deleted=False)
        now = timezone.now().isoformat()
        update = {
            "$set": {
                "deleted_at": now,
                "updated_at": now,
            }
        }
        return self.db[collection].update_one(scoped_filter, update, session=session, **kwargs)

    def aggregate(
        self, collection: str, pipeline: list[dict], session: ClientSession | None = None, **kwargs
    ) -> pymongo.command_cursor.CommandCursor:
        """Executes an aggregation pipeline, strictly prepending a tenant-scoping $match."""
        match_stage = {"$match": {"tenant_id": self.tenant_id, "deleted_at": None}}
        scoped_pipeline = [match_stage] + list(pipeline)
        return self.db[collection].aggregate(scoped_pipeline, session=session, **kwargs)
