# Forge: Architecture Specification & Master Execution Plan
*A Multi-Tenant Custom Objects & Workflow Studio Platform*

---

## 1. Executive Summary & Core Architectural Goals

**Forge** is a multi-tenant platform that enables tenants to define custom domain entities (schemas, validations, relationships, state transitions, and role-based permissions) and automatically generates:
1. **Dynamic, versioned, validated REST APIs** without per-type Django models.
2. **Dynamic React UI components** (`<SchemaForm>`, `<SchemaTable>`, workflow state badges and transition triggers) driven directly by compiled schemas.
3. **Guaranteed transactional side-effects** via the Transactional Outbox pattern into Standard SQS with dead-letter queueing (DLQ) and SSRF-hardened webhook dispatchers.

### Core Engineering Invariants
- **Strict Tenant Isolation:** Data access is gated through `TenantScopedRepository`. Aggregations, queries, inserts, updates, and `$lookup` joins strictly inject and validate `tenant_id`. Multi-tenant users supply `X-Tenant-Id`, validated against Postgres `TenantMembership`. Cross-tenant queries return `404 Not Found`. Direct database collection access in views is forbidden by test and AST lint rules.
- **Single-Transaction Atomic Idempotency (Principal-Scoped):** Requests using `Idempotency-Key` are scoped by `(tenant_id, principal_id, key)` with payload hash over `(method, path, body)`. Inside a single MongoDB transaction, the completed key document, record, outbox event, in-transaction audit entry, and unique keys commit together. Only 2xx responses are cached. `DuplicateKeyError` is caught outside the transaction, inspecting whether `idempotency_keys` (cached replay) or `unique_keys` (`409 Conflict`) raised it.
- **Optimistic Concurrency Control:** Lost updates are prevented via HTTP `If-Match` ETags matched against record version counters. Missing `If-Match` returns `428 Precondition Required`; stale edits return `412 Precondition Failed`.
- **Global Dynamic Uniqueness (`unique_keys` Collection):** Dynamic unique fields use a dedicated `unique_keys` collection (`{ tenant_id, type, field, value_hash }`) backed by a single unique compound index. Values are normalized (trimmed, lowercased) and hashed via keyed HMAC (`HMAC_SHA256(server_secret, normalized_value)`). PATCH updates atomically delete the old key and insert the new key inside the record transaction. On soft-delete, keys are removed.
- **Compound Wildcard Indexing (`data.$**`):** Filtering across arbitrary dynamic fields uses MongoDB 7.0+ compound wildcard indexing (`{ tenant_id: 1, type: 1, "data.$**": 1 }`), verified with `explain()`. Filters support equality, `[gte]`, `[lte]`, and `[in]`.
- **ReDoS Protection:** Custom regex validation rules strictly use `google-re2` (linear-time guarantee, zero backtracking, rejecting backreferences/lookarounds) with pattern length capped at 100 characters.
- **Relay Worker Resilient Claim Lease:** The outbox relay worker polls using `{$or: [{status: "pending"}, {status: "in_flight", locked_until: {$lt: now}}]}`, sorting by `created_at`. This prevents crashing workers from stranding events in `in_flight` forever.
- **SSRF-Hardened Webhook Dispatcher:** Webhook endpoints must be `https://`, validated against `ipaddress.is_global`, connect via IP-pinning with TLS SNI preserved, disallow redirects, and sign `timestamp + "." + payload` with HMAC-SHA256. Changed field names are role-masked so sensitive field names do not leak. Webhook secret is encrypted at rest and shown once on creation. Failed messages route to SQS DLQ with native SQS redrive.
- **Thin Outbox Events (No PII Leakage):** Outbox rows contain minimal event metadata (`event_id`, `tenant_id`, `type`, `record_id`, `version`, `action`, `changed_fields`). Consumers fetch the record under the subscriber's assigned permission profile before dispatching.
- **In-Transaction Audit Logging:** Audit records are committed atomically inside the record write transaction using the same `event_id` as the outbox row, guaranteeing 100% audit durability without depending on queue consumers.
- **Early "Walking Skeleton" Deployment:** Deploy a live walking skeleton (health check, Mongo Atlas/Docker, Postgres, Terraform, CI/CD) to AWS by the end of Phase 1, and integrate a bare-bones UI slice by day 12 to eliminate late integration surprises.

---

## 2. System Architecture & Component Diagram

```mermaid
flowchart TD
    subgraph ClientLayer ["Client & Design System"]
        UI["React 19 + TypeScript Studio"]
        Lib["@forge/schema-renderer (Storybook)"]
        UI --> Lib
    end

    subgraph APILayer ["API & Control Plane (Django / DRF)"]
        GW["Tenant Identification Middleware (Short-Lived JWT / Hashed ApiKey / X-Tenant-Id)"]
        Logger["Structured JSON Logger (request_id, tenant_id, principal_id)"]
        Throttle["Per-Tenant Throttling (MongoDB Token Bucket / DB Cache)"]
        RBAC["Field-Level Permission & Masking Engine"]
        SchemaCache["In-Process LRU Schema Cache (Immutable Versions)"]
        GenericViewSet["Generic Dynamic ViewSet (/api/v1/o/{type}/)"]
        WorkflowEngine["AST JSONLogic State Machine (google-re2)"]
        IdempotencyManager["Single-Transaction Idempotency Engine (Principal-Scoped)"]

        GW --> Logger --> Throttle --> RBAC --> GenericViewSet
        GenericViewSet --> SchemaCache
        GenericViewSet --> WorkflowEngine
        GenericViewSet --> IdempotencyManager
    end

    subgraph DataPlane ["Persistence Tier"]
        Postgres[("PostgreSQL 16\n(Users, Tenants, Roles, Memberships, ApiKeys, WebhookEndpoints)")]
        Mongo[("MongoDB 7.0+ Replica Set\n(ObjectTypes, Records, UniqueKeys, Outbox, AuditEvents)")]
    end

    subgraph AsyncPlane ["Event Pipeline & Workers"]
        Relay["Outbox Relay Worker (Lease with pending OR expired in_flight)"]
        SQS["AWS Standard SQS + Native Redrive DLQ / LocalStack"]
        WebhookConsumer["Webhook Dispatcher (SSRF is_global + IP Pinning + Role-Masked)"]
        ExportConsumer["CSV Exporter (Deterministic S3 Key)"]
    end

    ClientLayer -->|REST + JSONSchema| GW
    GW -.->|Auth & Membership Verification| Postgres
    GenericViewSet -->|TenantScopedRepository\n(Unit of Work: session.with_transaction)| Mongo
    Mongo -.->|Poll & Claim via locked_until| Relay
    Relay -->|Publish Event (at-least-once)| SQS
    SQS --> WebhookConsumer
    SQS --> ExportConsumer
    WebhookConsumer -.->|Dead-Letter| DLQ[SQS Dead-Letter Queue]
```

---

## 3. Detailed Architectural Specifications

### 3.1 Schema Registry, Versioning & Validation
- **Field Types:** `text`, `number`, `boolean`, `date`, `enum`, `reference`, `list`.
- **Field Descriptors:** `name`, `label`, `type`, `required`, `unique`, `default`, `validation_rules` (regex, min, max), `ui_hints` (widget type, placeholder, hidden, read_only).
- **ReDoS Protection:** All regex validation rules strictly use `google-re2`. Backreferences and lookarounds are rejected at schema validation time. Pattern length is capped at 100 characters.
- **Immutability Principle:** Published schemas (`status: "published"`) are immutable. Each published version increments monotonically (`v1`, `v2`, etc.).
- **In-Process LRU Caching:** Compiled JSON Schemas are cached in an in-process LRU cache (`functools.lru_cache`) keyed by `(tenant_id, type_slug, version)`. Immutable versions eliminate the need for Redis cache invalidation.
- **Compatibility Checker (`compat_checker.py`):**
  - **Non-breaking:** Adding an optional field, adding an enum value, loosening validation rules.
  - **Breaking:** Deleting/renaming a field, changing field type, adding a required field with no default, tightening validation rules, removing an enum value.
  - Breaking changes require an explicit major version increment and a resumable background backfill job.

### 3.2 Dynamic Records API, Indexing & Dynamic Uniqueness (Phase 2 Unit of Work)
- **Generic ViewSet Routing:**
  - `GET /api/v1/o/{type}/` (List & Filter)
  - `POST /api/v1/o/{type}/` (Create)
  - `GET /api/v1/o/{type}/{id}/` (Retrieve)
  - `PATCH /api/v1/o/{type}/{id}/` (Partial Update - Requires `If-Match`, returns `428` if missing)
  - `DELETE /api/v1/o/{type}/{id}/` (Soft Delete: sets `deleted_at: timestamp`)
  - `POST /api/v1/o/{type}/{id}/transitions/{transition_name}/` (Execute Workflow Transition)
- **Multi-Document Transaction Unit-of-Work (Built in Phase 2):**
  All mutating record operations (`POST`, `PATCH`, `DELETE`, workflow transitions) execute inside `session.with_transaction()` committing:
  1. Record write / update / soft-delete in `records`.
  2. Dynamic unique keys insertion / replacement / deletion in `unique_keys`.
  3. In-transaction immutable audit entry in `audit_events`.
  4. Thin outbox event in `outbox`.
- **Dynamic Uniqueness Engine (`unique_keys` Collection):**
  - Schema:
    ```json
    {
      "tenant_id": "tenant_123",
      "type": "customer",
      "field": "email",
      "value_hash": "hmac_sha256(server_secret, normalized_value)",
      "record_id": "rec_456"
    }
    ```
  - **Single Compound Unique Index:** `{ tenant_id: 1, type: 1, field: 1, value_hash: 1 }` in `unique_keys`.
  - **Normalization:** Strings are trimmed and lowercased before hashing to prevent casing collisions.
  - **Keyed Hash:** Value is hashed using `HMAC_SHA256(server_secret, normalized_value)` to prevent dictionary attacks on PII values like emails.
  - **PATCH Update Flow:** If a PATCH alters a unique field, the old key document is deleted and the new key document is inserted inside the same transaction.
  - **Soft-Delete Flow:** When a record is soft-deleted, its entries in `unique_keys` are deleted inside the transaction, freeing the unique value for future records.
- **PATCH Validation Semantics:**
  1. Fetch existing record via `TenantScopedRepository`.
  2. Apply partial patch in-memory to generate the merged document.
  3. Validate the **merged document** against compiled JSON Schema.
  4. Write only updated dot-paths to MongoDB using `$set: { "data.<field>": val }`.
- **Query Hardening & Pagination:**
  - Whitelist-only filtering with support for equality, `[gte]`, `[lte]`, and `[in]` (e.g. `data.status[in]=active,pending`).
  - Whitelist-only sorting (`sort=+data.<field>` or `sort=-data.<field>`).
  - **Composite Cursor Pagination:** Encodes `(sort_value, _id)` to guarantee stable cursor pagination across custom-sorted datasets.
  - Rejection of MongoDB injection operators (`$where`, `$regex`, `$gt`, etc.).
- **Compound Wildcard Indexing:**
  - Index: `{ tenant_id: 1, type: 1, "data.$**": 1 }` (MongoDB 7.0+). Verified using `explain()`.
  - Evaluated in benchmarks for both query read performance and write amplification.

### 3.3 Tenancy Hardening, Auth & RBAC
- **Tenant Identification:**
  - Authenticated via short-lived JWT (15-minute expiry) or machine-to-machine `ApiKey` (stored in Postgres as SHA-256 hash).
  - Users belonging to multiple tenants pass `X-Tenant-Id: <tenant_id>`, validated against Postgres `TenantMembership`.
- **Complete `TenantScopedRepository` Interface:**
  All MongoDB interactions strictly pass through the repository, which enforces `tenant_id` and forwards PyMongo sessions:
  ```python
  class TenantScopedRepository:
      def __init__(self, tenant_id: str, db: Database):
          if not tenant_id:
              raise ValueError("TenantScopedRepository requires authenticated tenant_id")
          self.tenant_id = tenant_id
          self.db = db

      def find_one(self, collection: str, query: dict, session=None, **kwargs):
          return self.db[collection].find_one(
              {**query, "tenant_id": self.tenant_id, "deleted_at": None}, session=session, **kwargs
          )

      def find_with_deleted(self, collection: str, query: dict, session=None, **kwargs):
          return self.db[collection].find_one(
              {**query, "tenant_id": self.tenant_id}, session=session, **kwargs
          )

      def find(self, collection: str, query: dict, session=None, **kwargs):
          return self.db[collection].find(
              {**query, "tenant_id": self.tenant_id, "deleted_at": None}, session=session, **kwargs
          )

      def insert_one(self, collection: str, document: dict, session=None, **kwargs):
          if "tenant_id" in document and document["tenant_id"] != self.tenant_id:
              raise ValueError("Mismatched tenant_id in document")
          document["tenant_id"] = self.tenant_id
          return self.db[collection].insert_one(document, session=session, **kwargs)

      def update_one(self, collection: str, filter_query: dict, update: dict, session=None, **kwargs):
          scoped_filter = {**filter_query, "tenant_id": self.tenant_id, "deleted_at": None}
          return self.db[collection].update_one(scoped_filter, update, session=session, **kwargs)

      def delete_one(self, collection: str, filter_query: dict, session=None, **kwargs):
          scoped_filter = {**filter_query, "tenant_id": self.tenant_id}
          return self.db[collection].delete_one(scoped_filter, session=session, **kwargs)

      def aggregate(self, collection: str, pipeline: list[dict], session=None, **kwargs):
          scoped_pipeline = [{"$match": {"tenant_id": self.tenant_id, "deleted_at": None}}] + pipeline
          return self.db[collection].aggregate(scoped_pipeline, session=session, **kwargs)
  ```
- **Anti-Enumeration 404:** Any query against a record belonging to another tenant or a non-existent record returns `404 Not Found` (never `403 Forbidden`).
- **AST Lint & Test Enforcement:** A pytest AST check verifies that view code and serializers never import `pymongo.collection.Collection` or call `db[...]` directly.
- **Throttling:** Per-tenant DRF rate limiting backed by a MongoDB token bucket collection or Django DB cache.
- **Field-Level Masking:** Restricted fields are removed during serialization across all egress paths: API responses, CSV exports, and webhook payloads.
- **In-Transaction Audit Durability & Masking:**
  - Written directly in the record transaction to `audit_events` (using the same `event_id` as the outbox row).
  - Sensitive masked values are stored as SHA-256 hashes of the value or masked on read according to the requesting user's role.

### 3.4 Concurrency & Principal-Scoped Single-Transaction Idempotency
- **Optimistic Concurrency Control:**
  - Records carry an integer `version` field.
  - Read endpoints return `ETag: W/"<version>"`.
  - Write/update endpoints require `If-Match: W/"<version>"`. Missing header returns `428 Precondition Required`.
  - Atomically executed via `findOneAndUpdate({"_id": id, "tenant_id": tenant, "version": current_version}, {"$inc": {"version": 1}, "$set": updates})`.
  - Mismatched version returns `412 Precondition Failed`.
- **Principal-Scoped Single-Transaction Idempotency:**
  - Unique Index: `{ tenant_id: 1, principal_id: 1, key: 1 }` on `idempotency_keys` collection.
  - Request Hash: `sha256(method + ":" + path + ":" + request_body)`.
  - Scoping by `principal_id` ensures that a Viewer reusing an Admin's key cannot access the Admin's unmasked cached response.
  - Only successful 2xx responses are cached.
  - Executed inside `session.with_transaction()`:
    1. Insert completed key document: `{ tenant_id, principal_id, key, request_hash, status_code, response_body, created_at, expires_at: now + 24h }`.
    2. Write record, unique keys, thin outbox event, and in-transaction audit entry.
  - **DuplicateKeyError & Conflict Handling Outside Transaction:**
    - `session.with_transaction()` automatically retries transient write conflicts.
    - If a non-transient `DuplicateKeyError` is raised, it propagates outside the transaction:
      - **Check `idempotency_keys` index:** If `idempotency_keys` index raised the error, read the existing key. If request hash matches, replay cached response (200/201). If request hash differs, return `422 Unprocessable Entity`.
      - **Check `unique_keys` index:** If `unique_keys` index raised the error, return `409 Conflict` (e.g. `{"detail": "Field <name> value already exists"}`).
    - **Side-Effect Free Callback Rule:** The transaction callback contains strictly zero external side effects (no external HTTP calls, no email sending, no non-debug logging).

### 3.5 Deterministic Workflow Engine
- **State Machine Definition:** Per object type, tenants define states and authorized transitions.
- **Tree-Walking JSONLogic Interpreter:**
  - Whitelisted operators: `==`, `!=`, `>`, `<`, `>=`, `<=`, `and`, `or`, `not`, `in`, `var`.
  - Dynamic variable lookup: `{"in": ["Finance", {"var": "user.roles"}]}`.
  - Guardrails: Maximum recursion depth ($\le 10$) and node count ($\le 50$). Zero usage of Python `eval()`.
- **Atomic Transition Execution:**
  - Bundled inside the multi-document unit of work with outbox and audit entry.
  - Uses compare-and-set: `findOneAndUpdate({"_id": id, "tenant_id": tenant, "state": expected_from_state, "version": current_version}, {"$set": {"state": to_state}, "$inc": {"version": 1}})`.
  - If state or version does not match, transaction aborts with **`409 Conflict`**.

### 3.6 Transactional Outbox, Relay Worker & Webhook Dispatcher
- **Thin Outbox Event Structure:**
  ```json
  {
    "event_id": "uuid4",
    "tenant_id": "tenant_123",
    "topic": "records.invoice.created",
    "action": "created",
    "record_id": "rec_456",
    "version": 1,
    "changed_fields": ["status", "total"],
    "tombstone": null,
    "status": "pending",
    "locked_until": null,
    "created_at": "ISO8601"
  }
  ```
  *(For deleted records, `tombstone: { record_id, type, deleted_at }` is stored in the thin event).*
- **Relay Worker Resilient Claim Lease:**
  - To prevent stranding events when a worker crashes mid-flight, the claim filter matches:
    `findOneAndUpdate({ $or: [{ status: "pending" }, { status: "in_flight", locked_until: { $lt: now } }] }, { $set: { status: "in_flight", locked_until: now + 30s } }, sort=[("created_at", 1)])`.
  - Publishes event to **Standard AWS SQS** with `event.version` for receiver ordering.
  - Upon SQS publish confirmation: updates to `status: "published", published_at: now`.
  - MongoDB TTL index on `published_at` (7 days) purges historical outbox rows.
- **Webhook Dispatcher & SSRF Defense:**
  - **Endpoint Model (Postgres):** `url`, `secret` (encrypted at rest with AES-GCM / KMS, shown once on creation), `event_types`, and `role` (permission profile).
  - **Role-Masked Payloads:** When generating dispatch payload, fetch record under endpoint's `role` and filter `changed_fields` by the endpoint's role so sensitive field names do not leak.
  - **SSRF Hardening:**
    - Scheme must be `https://`.
    - Validate destination IP with Python `ipaddress.ip_address(ip).is_global`.
    - IP Pinning with custom transport: connects directly to validated IP while preserving SNI hostname for TLS verification.
    - Disallow redirects (`allow_redirects=False`) and set a 5-second timeout.
    - Signature: `X-Forge-Signature: t=<timestamp>,v1=HMAC_SHA256(secret, t + "." + payload)`.
    - Native SQS redrive for DLQ replay.
- **CSV Exporter Consumer:**
  - Streams tenant records directly to S3 under a deterministic S3 key (`/exports/{tenant_id}/{export_id}.csv`) guaranteeing idempotent consumer retries.

### 3.7 UI Components & Schema Builder Studio
- **Package `@forge/schema-renderer` (TypeScript + React 19):**
  - `<SchemaForm schema={schema} onSubmit={...} permissions={permissions} />`: Renders form fields with client-side validation and permission-based masking.
  - `<SchemaTable schema={schema} records={records} onSort={...} onFilter={...} />`: Renders sortable, filterable records with cursor pagination.
  - `<WorkflowActions currentState={record.state} transitions={transitions} onTransition={...} />`: Renders state badge and authorized transition buttons.
  - Documented and tested with **Storybook**.
- **Schema Builder Web App (Pragmatic Scope):**
  - Schema Editor: Add/edit fields, validations, and configure field order using **Up / Down buttons**.
  - Workflow Editor: Configure states, transitions, role guards, and render state machine diagrams using **Mermaid**.
  - Dynamic Preview: Side-by-side live `<SchemaForm>` preview reacting to schema edits.

---

## 4. Invariant Verification Test Matrix

These 7 automated test suites demonstrate the resilience and correctness of the platform:

| # | Invariant Property | Failure Mode Prevented | Test Implementation Strategy |
|---|---|---|---|
| **1** | **Tenant Isolation** | Data leakage across organizations | Parameterized matrix test iterating every route, HTTP method, and foreign tenant JWT. Asserts `404 Not Found` across 100% of cases. AST lint rule asserts zero direct collection access. |
| **2** | **Single-Transaction Idempotency** | Duplicate records from retries & race conditions | 50 concurrent requests with identical `Idempotency-Key`: exactly 1 record created; 1 request receives `201 Created`, 49 receive cached replay. No stranded in-progress keys. Verify `unique_keys` vs `idempotency_keys` error differentiation. |
| **3** | **No Lost Updates** | Overwriting concurrent modifications | Two simultaneous PATCH requests with initial `ETag`. First succeeds (200), second fails with `412 Precondition Failed`. Missing `If-Match` returns `428`. |
| **4** | **Schema Evolution Safety** | Corrupted records & breaking changes | Breaking change without migration rejected. Non-breaking changes succeed. Legacy records remain readable. ReDoS patterns with backreferences rejected by `google-re2`. |
| **5** | **No Lost Events Under Crash** | Event loss on relay worker death | Kill relay worker at two failure points: (1) before publish to SQS, and (2) after publish before marking published. Assert expired `in_flight` rows are reclaimed by replacement relay worker; zero lost events and zero duplicate effects on internal stores. |
| **6** | **Field Masking Egress** | Sensitive data leakage across channels | Query as `Viewer` role on record with masked fields. Asserts fields and modified field names are absent from REST responses, CSV exports, audit logs, and webhooks. |
| **7** | **Atomic Workflow Guards** | Race conditions during approval state change | Concurrent `approve` and `reject` transitions on same record state. Asserts atomic CAS produces exactly 1 state change and 1 `409 Conflict`. |

---

## 5. Repository Layout (Monorepo)

```
Custom_Objects_Workflow_Studio/
├── .github/
│   └── workflows/
│       ├── backend-ci.yml           # Pytest, linters, isolation matrix, AST check
│       ├── frontend-ci.yml          # Vitest, Storybook build, typecheck
│       ├── docker-build.yml         # Container build & smoke test
│       └── deploy-skeleton.yml      # Walking skeleton deploy to AWS
├── apps/
│   ├── api/                         # Django / DRF Control Plane & Records API
│   │   ├── manage.py
│   │   ├── config/                  # Settings, wsgi, asgi, urls
│   │   ├── apps/
│   │   │   ├── authentication/      # Postgres models: User, Tenant, Role, Membership, ApiKey
│   │   │   ├── schemas/             # Schema registry, JSON Schema compiler, compat checker
│   │   │   ├── records/             # Generic ViewSet, ETag, filter/sort/composite cursor
│   │   │   ├── workflows/           # State machine, AST JSONLogic rule evaluator (re2)
│   │   │   ├── outbox/              # Outbox persistence, transaction wrappers
│   │   │   └── webhooks/            # Webhook registration and delivery tracking
│   │   ├── core/
│   │   │   ├── repository.py        # Complete TenantScopedRepository (PyMongo wrapper)
│   │   │   ├── idempotency.py       # Single-transaction idempotency engine
│   │   │   ├── permissions.py       # Field-level RBAC & masking serializers
│   │   │   ├── middleware.py        # Tenant identification, X-Tenant-Id, structured JSON logging
│   │   │   └── throttling.py        # Per-tenant rate limiting
│   │   ├── Dockerfile
│   │   └── pyproject.toml
│   ├── workers/                     # Async Event Workers
│   │   ├── relay/                   # Outbox -> SQS publisher with resilient lease mechanism
│   │   ├── consumers/
│   │   │   ├── webhook_dispatcher.py# SSRF-hardened dispatcher (is_global + IP pinning + role-masked + DLQ)
│   │   │   └── csv_exporter.py      # S3 streaming exporter (deterministic key)
│   │   └── Dockerfile
│   └── web/                         # React Application (Studio & Record Manager)
│       ├── src/
│       │   ├── app/                 # Router, tenant layout, auth provider
│       │   ├── pages/               # Schema builder, record list, record detail
│       │   └── hooks/               # React Query hooks for schemas & records
│       ├── package.json
│       └── vite.config.ts
├── packages/
│   └── schema-renderer/             # Reusable UI Component Library
│       ├── src/
│       │   ├── SchemaForm.tsx       # Dynamic form with validation
│       │   ├── SchemaTable.tsx      # Dynamic data table
│       │   ├── WorkflowBadge.tsx    # State indicator & transition buttons
│       │   └── types.ts
│       ├── .storybook/              # Storybook configuration
│       ├── package.json
│       └── tsconfig.json
├── infra/
│   ├── docker/
│   │   ├── docker-compose.yml       # Postgres 16, Mongo 7 Replica Set, LocalStack
│   │   └── mongo-init/              # Replica set init script
│   └── terraform/                   # AWS deployment infrastructure (used from Phase 1)
│       ├── main.tf
│       ├── variables.tf
│       └── outputs.tf
├── tests/
│   ├── isolation/                   # Automated cross-tenant adversarial matrix & AST lint
│   ├── concurrency/                 # Idempotency & ETag lost-update suites
│   ├── chaos/                       # Relay crash recovery & deduplication test
│   └── load/                        # k6 performance test scripts
├── docs/
│   ├── adr/                         # Architecture Decision Records
│   │   ├── 001-hybrid-postgres-mongo-django-control-plane.md
│   │   ├── 002-transactional-outbox-sqs-vs-cdc.md
│   │   ├── 003-etag-concurrency-vs-pessimistic-locking.md
│   │   ├── 004-ast-rule-evaluation-vs-eval.md
│   │   └── 005-thin-events-vs-fat-outbox-payloads.md
│   └── diagrams/
├── README.md
└── Makefile                         # make up, make test, make lint, make load-test
```

---

## 6. Architecture Decision Records (ADRs)

1. **ADR 001: Hybrid Relational & Document Storage (Why Django if records bypass the ORM?)**
   - *Context:* Django's ORM is designed for static relational models.
   - *Decision:* Use PostgreSQL + Django ORM for users, tenants, roles, memberships, API keys, and webhook endpoints. Use MongoDB + PyMongo for dynamic tenant records, unique keys, outbox, and audit events.
   - *Rationale:* Django provides battle-tested session/auth management, migrations for control-plane metadata, middleware, DRF permission pipelines, and throttling. Records bypass the ORM into MongoDB to support arbitrary tenant-defined shapes without dynamic SQL migrations (`ALTER TABLE`).
2. **ADR 002: Transactional Outbox Relay to Standard SQS vs CDC / Kafka**
   - *Context:* Need reliable event publishing from database writes.
   - *Decision:* Persist outbox events in MongoDB within the record transaction; use a leased polling relay to publish to AWS Standard SQS with native redrive DLQ.
   - *Rationale:* Standard SQS provides high throughput, native DLQ support, and zero operational maintenance. Consumers handle out-of-order deliveries using `event.version` and deduplicate via `event_id`. The outbox pattern decouples the architecture from MongoDB oplog access privileges required by CDC.
3. **ADR 003: ETag Optimistic Concurrency vs Pessimistic Locking**
   - *Context:* Need to prevent lost updates on concurrent record edits.
   - *Decision:* Use HTTP `If-Match` with record version numbers. Missing header returns `428 Precondition Required`.
   - *Rationale:* Stateless, horizontally scalable across web workers, and eliminates database row lock contention. Returns standard `412 Precondition Failed` on version mismatches.
4. **ADR 004: Tree-Walking AST Rule Evaluation vs Eval / Script Engines**
   - *Context:* Workflow transitions require conditional evaluation of tenant-defined rules.
   - *Decision:* Implement a bounded JSONLogic interpreter using an AST traversal over a strict operator whitelist, with regex rules validated via `google-re2`.
   - *Rationale:* Eliminates Remote Code Execution (RCE) risks inherent in Python's `eval()` and eliminates ReDoS vulnerabilities. Bounded recursion depth ($\le 10$) and node count ($\le 50$) prevent algorithmic DoS.
5. **ADR 005: Thin Events vs Fat Payloads in Outbox**
   - *Context:* Masked/PII fields must not be exposed to unauthorized subscribers.
   - *Decision:* Write only thin event metadata to the outbox (`event_id`, `record_id`, `changed_fields`, `tombstone`).
   - *Rationale:* Prevents raw sensitive data from sitting unmasked in MongoDB outbox collections or SQS queues. Consumers fetch and mask data dynamically according to subscriber access profiles.

---

## 7. Phased Implementation Roadmap

### Phase 0: Foundations, Infrastructure & Isolation Harness (Days 1–3)
- Initialize monorepo structure.
- Docker Compose: PostgreSQL 16, MongoDB 7.0 (single-node replica set for transactions), LocalStack (SQS/S3).
- Django control plane: Custom User, Tenant, Role, Membership, and ApiKey models in Postgres.
- Structured JSON logging middleware (`request_id`, `tenant_id`, `principal_id`) and health check endpoints (`/healthz`, `/readyz`).
- Complete `TenantScopedRepository` wrapping PyMongo with strict tenant isolation, validation, and session forwarding.
- Multi-tenant tenant selection: `X-Tenant-Id` header validated against Postgres `TenantMembership`.
- **Foundational Tests:**
  1. Adversarial matrix test: Every endpoint returns `404 Not Found` for foreign tenant tokens.
  2. AST lint test: No direct `pymongo.collection.Collection` usage in API views.

### Phase 1: Schema Registry, Versioning & Walking Skeleton Deploy (Days 4–7)
- Mongo `object_types` collection with schema validation.
- Schema publishing workflow (`draft` -> immutable `published` versions).
- In-process LRU cache for compiled JSON Schemas keyed by `(tenant_id, type_slug, version)`.
- `CompatibilityChecker` classifying non-breaking vs breaking changes.
- ReDoS protection with `google-re2` (max pattern length 100, no lookarounds/backreferences).
- Resumable backfill job scaffold for schema version upgrades.
- **Milestone: AWS Walking Skeleton Deploy via Terraform:** Deploy containerized API with `/healthz` and MongoDB connection to AWS via GitHub Actions using Terraform.

### Phase 2: Dynamic Records API, Wildcard Indexing & Dynamic Uniqueness (Days 8–12)
- Generic DRF ViewSet `/api/v1/o/{type}/` validating writes against compiled JSON Schema.
- **Multi-Document Transaction Unit of Work:** Unified transaction writing records, dynamic unique keys, in-transaction audit log, and outbox rows via `session.with_transaction()`.
- Merged-document PATCH validation with dotted `$set` MongoDB updates.
- Compound wildcard index initialization: `{ tenant_id: 1, type: 1, "data.$**": 1 }`. Verify with `explain()`.
- **Dynamic Uniqueness Engine:** Dedicated `unique_keys` collection with `{ tenant_id: 1, type: 1, field: 1, value_hash: 1 }` unique index, normalized (trimmed, lowercase) and hashed via `HMAC_SHA256(server_secret, val)`. Handles PATCH replacement and soft-delete removal.
- Whitelisted filtering (`=`, `[gte]`, `[lte]`, `[in]`), sorting, and composite cursor-based pagination `(sort_value, _id)`.
- References resolution and tenant-isolated `$lookup` joins.
- ETag generation, `If-Match` validation (`412`), and missing `If-Match` rejection (`428 Precondition Required`).
- **Milestone (Day 12): Bare-bones UI slice:** Minimal React interface fetching schemas and listing records.

### Phase 3: RBAC, Field Masking & Single-Transaction Idempotency (Days 13–16)
- Roles: `Admin`, `Editor`, `Viewer`.
- Field-level read/write permissions with recursive serialization masking across REST endpoints.
- Principal-scoped single-transaction `Idempotency-Key` engine:
  - Scoped by `(tenant_id, principal_id, key)` with `sha256(method + ":" + path + ":" + body)`. Caches only 2xx responses.
  - Zero side-effects inside transaction callback.
  - Catches `DuplicateKeyError` outside transaction, differentiating `idempotency_keys` replay from `unique_keys` 409 conflict.
- Per-tenant throttling backed by MongoDB token bucket / DB cache.
- Concurrency test: 50 simultaneous identical requests producing 1 record and 49 cached replays.

### Phase 4: Deterministic Workflow Engine (Days 17–20)
- Workflow definition schema (states, transitions, role guards, JSONLogic rules).
- Tree-walking AST rule evaluator with recursion depth ($\le 10$) and node count ($\le 50$) limits.
- Atomic state transition execution using `session.with_transaction()` compare-and-set.
- Uniform `409 Conflict` on lost transition races.

### Phase 5: Transactional Outbox, SQS Pipeline & Consumers (Days 21–26)
- Resilient outbox relay worker using claim filter: `{$or: [{status: "pending"}, {status: "in_flight", locked_until: {$lt: now}}]}`, polling and publishing to Standard SQS with `event.version`.
- TTL index on `published_at` for outbox cleanup.
- SQS consumer framework with deduplication ledger by `event_id`.
- **Consumers:**
  1. SSRF-hardened Webhook Dispatcher (validated with `ipaddress.is_global`, IP pinning with TLS SNI preserved, encrypted secrets, role-masked payloads, HMAC anti-replay signature, native SQS DLQ redrive).
  2. CSV Streaming Exporter direct to S3 (deterministic S3 keys).
- Chaos test: Kill relay worker at two failure points (before SQS publish, and after SQS publish before outbox status update) and assert zero lost events and zero duplicate effects on internal stores.

### Phase 6: UI Component Package & Schema Builder Studio (Days 27–34)
- Package `@forge/schema-renderer`:
  - `<SchemaForm>` with client validation and field permission awareness.
  - `<SchemaTable>` with sort, filter, and pagination controls.
  - `<WorkflowActions>` with state badge and transition buttons.
  - Storybook catalog with interactive component stories.
- Web Studio App:
  - Schema Editor: Add/edit fields, validations, and configure field order using **Up / Down buttons**.
  - Workflow Editor: Configure states, transitions, role guards, and render state machine diagrams using **Mermaid**.
  - Dynamic Record Explorer.

### Phase 7: Production Hardening, Load Testing & Documentation (Days 35–40)
- Complete Terraform configuration for AWS infrastructure.
- GitHub Actions CI/CD pipeline (lint, test, build, containerize).
- k6 load tests measuring create/list p50/p95 latency, explain() plans, and wildcard index write amplification (tested on Docker/dedicated cluster, not free Atlas).
- 5 Architecture Decision Records (ADRs).
- Benchmark metrics and comprehensive README documentation.
