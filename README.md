# Forge: Multi-Tenant Custom Objects & Workflow Studio

[![CI/CD](https://github.com/SambitAmanta/Custom-Objects-Workflow-Studio/actions/workflows/backend-ci.yml/badge.svg)](https://github.com/SambitAmanta/Custom-Objects-Workflow-Studio/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11](https://img.shields.io/badge/Python-3.11-brightgreen.svg)](https://www.python.org/)
[![Django 5.1](https://img.shields.io/badge/Django-5.1-092E20.svg)](https://www.djangoproject.com/)
[![MongoDB 7.0](https://img.shields.io/badge/MongoDB-7.0%2B%20ReplicaSet-green.svg)](https://www.mongodb.com/)
[![React 19](https://img.shields.io/badge/React-19-61DAFB.svg)](https://react.dev/)

> **Tenants define their own object types (fields, validation, relationships, approval workflows). The platform generates versioned, validated REST APIs, forms, and tables from that single schema—with strict tenant isolation, single-transaction idempotency, optimistic concurrency, and resilient event-driven side effects.**

---

## 1. System Architecture

```
React Studio App + @forge/schema-renderer
                 │
                 ▼  (Bearer JWT + X-Tenant-Id)
        Django / DRF Control Plane
        ┌────────────────────────────────────────────────────────┐
        │  • TokenBucket Rate Throttling                         │
        │  • TenantScopedRepository (AST collection guard)       │
        │  • FieldPermissionManager (read/write/masked/hidden)   │
        │  • Draft-07 JSON Schema Validation                     │
        │  • ETag Optimistic Concurrency (RFC 7232 / 6585)       │
        │  • Safe AST JSONLogic Evaluator (Zero eval / Re2)      │
        └──────────────┬──────────────────────────┬──────────────┘
                       │                          │
        (ORM Models)   ▼                          ▼  (PyMongo Multi-Doc Transactions)
     PostgreSQL 16 Storage                   MongoDB 7.0 Replica Set
    ┌──────────────────────┐                ┌──────────────────────────────────┐
    │ • auth_tenants       │                │ • object_types (versioned schemas)│
    │ • auth_users         │                │ • records (wildcard compound idx)│
    │ • auth_memberships   │                │ • unique_keys (HMAC-SHA256 hash) │
    │ • auth_api_keys      │                │ • audit_events (immutable audit) │
    │ • webhook_endpoints  │                │ • outbox (thin events + TTL)     │
    └──────────────────────┘                └────────────────┬─────────────────┘
                                                             │
                                          Atomic Leased Claim│
                                          {$or: [pending,    │
                                                 expired]}   ▼
                                                Outbox Relay Worker
                                                             │
                                                             ▼ (event.version)
                                                    AWS Standard SQS
                                                     │              │
                              ┌──────────────────────┘              └─────────────────────┐
                              ▼                                                           ▼
                SSRF-Hardened Webhook Dispatcher                             CSV Streaming S3 Exporter
                ┌──────────────────────────────────────┐                     ┌────────────────────────┐
                │ • DNS Resolution & IP Pinning        │                     │ • Deterministic S3 Key │
                │ • ipaddress.is_global validation     │                     │   tenants/{id}/exports │
                │ • TLS SNI & Host preservation        │                     │ • Low-memory stream    │
                │ • Anti-Replay HMAC-SHA256 signature  │                     └────────────────────────┘
                │ • Role-masked egress payloads        │
                │ • Native SQS Dead Letter Queue (DLQ) │
                └──────────────────────────────────────┘
```

---

## 2. Why This Architecture? (Technical Decisions & Recruiter FAQ)

| Layer | Choice | Architectural Justification |
| :--- | :--- | :--- |
| **API Framework** | **Django + DRF** | Serves dynamic schemas through one generic polymorphic ViewSet without database model generation. Provides production-grade auth, middleware, throttling pipelines, and control-plane management. |
| **Tenant Data** | **MongoDB 7.0+** | Stores polymorphic, tenant-defined document shapes without dynamic SQL DDL migrations (`ALTER TABLE`). Multi-document transactions guarantee atomic `record + unique_keys + outbox + audit` writes. |
| **Identity & Control Plane** | **PostgreSQL 16** | Relational integrity and constraints for users, tenant memberships, API keys, and webhook registrations. |
| **Queue** | **AWS Standard SQS** | AWS-native, highly available, cost-effective queuing with built-in Dead-Letter Queue (DLQ). Consumers deduplicate via a MongoDB ledger by `event_id`. |
| **UI Library** | **React 19 + TypeScript** | `@forge/schema-renderer`: A pure component package that compiles an object schema into interactive `<SchemaForm>`, `<SchemaTable>`, and `<WorkflowActions>`. |

---

## 3. The Seven Pillars of Distributed Reliability

### Pillar 1: Schema Registry & Versioning
- **Draft to Immutable Published Versions**: Schemas transition from editable drafts to frozen immutable published versions with strict monotonically incrementing versions.
- **7 Supported Field Types**: `text`, `number`, `boolean`, `date`, `enum`, `reference`, `list`.
- **Pre-Compiled JSON Schema**: Schemas compile into Draft-07 JSON Schema descriptors cached in-process via LRU cache.
- **Compatibility Checker**: Classifies changes as breaking (requires migration) vs non-breaking (adding optional fields, loosening rules).
- **Linear-Time ReDoS Protection**: All validation regex patterns are verified through Google's `re2` engine (C++ linear-time matching, max length 100, backreferences and lookarounds strictly forbidden).

### Pillar 2: Dynamic Records API & Optimistic Concurrency Control (OCC)
- **RFC 7232 / 6585 Compliance**: `GET` emits `ETag: W/"<version>"`. `PATCH` and `DELETE` strictly require `If-Match: W/"<version>"` (missing header returns `428 Precondition Required`). Version mismatches immediately abort with `412 Precondition Failed`.
- **Compound Wildcard Index**: Efficient querying and sorting over arbitrary tenant-defined nested fields via `{ tenant_id: 1, type: 1, "data.$**": 1 }`.
- **Composite Cursor Pagination**: Deterministic pagination over `(sort_value, _id)` eliminating `skip` performance degradation.

### Pillar 3: Dynamic Uniqueness Engine (Bypassing MongoDB's 64-Index Ceiling)
- Placing individual partial unique indexes per tenant-defined field causes MongoDB to hit its hard 64-index limit after ~30 object types, while partial filters without type scoping collide across different types.
- **Solution**: Dedicated `unique_keys` collection backed by a **single** unique compound index:
  `{ tenant_id: 1, type: 1, field: 1, value_hash: 1 }`
- Values are normalized (trimmed, lowercased) and hashed using `HMAC-SHA256(FORGE_SERVER_SECRET, normalized_val)` to eliminate rainbow-table attacks. Atomically cleaned up on soft deletes and PATCH mutations.

### Pillar 4: Single-Transaction Idempotency Engine
- Supports standard `Idempotency-Key` header scoped to `(tenant_id, principal_id, key)`.
- **Zero Crash Gap**: Idempotency key reservation, record write, unique key validation, audit log, and outbox event are executed in **one single MongoDB transaction**.
- Transient write conflicts are automatically retried by `session.with_transaction()`.
- Catches `DuplicateKeyError` outside the transaction to cleanly distinguish between an idempotency cached replay and a real 409 unique constraint violation.
- Caches only 2xx responses and checks payload hash matching (mismatched payload returns `422 Unprocessable Entity`).

### Pillar 5: Field-Level RBAC & Masking Engine
- Granular permission matrix per field per role (`admin`, `editor`, `viewer`):
  - `write`: Full read and mutation access.
  - `read`: Read-only access; write/patch rejected with `403 Forbidden`.
  - `masked`: Value replaced with `***MASKED***` across all REST and webhook responses.
  - `hidden`: Field key completely removed from serialization payloads.

### Pillar 6: Deterministic Workflow Engine
- Declarative state machine supporting custom transitions, role guards, and conditional rules.
- **Safe JSONLogic AST Evaluator**: Recursive tree-walking interpreter with bounded recursion depth ($\le 10$) and node count ($\le 50$), with **zero Python `eval()`**.
- Atomic compare-and-set state transitions inside multi-document transactions. Lost transition races uniformly return `409 Conflict`.

### Pillar 7: Resilient Transactional Outbox, SQS & SSRF-Hardened Consumers
- **Resilient Claim Lease**: The relay worker claims batches using:
  `{$or: [{status: "pending"}, {status: "in_flight", locked_until: {$lt: now}}]}`
  Expired in-flight leases are safely reclaimed without stranding events.
- **Thin Events**: Outbox events omit raw data payloads, preventing sensitive PII leakage through queues.
- **Deduplication Ledger**: SQS consumers maintain a MongoDB deduplication ledger on `(tenant_id, event_id, consumer_name)`, guaranteeing **zero duplicate effects**.
- **SSRF Defense**:
  - Resolves target hostnames via DNS and validates with `ipaddress.ip_address(ip).is_global`.
  - Blocks loopback (127.0.0.1), private RFC 1918 (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16), and cloud metadata (169.254.169.254).
  - Pins resolved IP while preserving TLS SNI and `Host` header.
  - Strictly disables redirects (`allow_redirects=False`) and enforces HTTPS.
  - Anti-replay timestamped HMAC-SHA256 signature (`X-Forge-Signature: t=...,v1=...`).

---

## 4. Test Suite & Reliability Matrix

The platform is fortified by **41 automated test suites** covering unit logic, adversarial isolation, high-concurrency races, and chaos injection:

```bash
============================= test session starts =============================
tests/chaos/test_relay_worker_crash.py::test_relay_crash_before_sqs_publish_zero_lost_events PASSED
tests/chaos/test_relay_worker_crash.py::test_relay_crash_after_sqs_publish_zero_duplicate_effects PASSED
tests/concurrency/test_etag_concurrency.py::test_missing_if_match_returns_428 PASSED
tests/concurrency/test_etag_concurrency.py::test_stale_etag_returns_412_precondition_failed PASSED
tests/isolation/test_field_masking_rbac.py::test_viewer_never_sees_hidden_fields PASSED
tests/isolation/test_field_masking_rbac.py::test_editor_sees_read_and_masked_fields PASSED
tests/isolation/test_field_masking_rbac.py::test_viewer_write_attempt_rejected_with_403 PASSED
tests/isolation/test_isolation_matrix.py::test_cross_tenant_token_tampering_rejected PASSED
tests/isolation/test_isolation_matrix.py::test_multi_tenant_user_header_switching PASSED
tests/isolation/test_isolation_matrix.py::test_api_key_tenant_isolation PASSED
tests/isolation/test_isolation_matrix.py::test_data_plane_cross_tenant_isolation_matrix PASSED
tests/isolation/test_records_api.py::test_record_crud_lifecycle_and_validation PASSED
tests/isolation/test_records_api.py::test_reference_expansion PASSED
tests/isolation/test_records_api.py::test_cross_tenant_record_access_returns_404 PASSED
tests/isolation/test_schema_apis.py::test_admin_can_save_draft_and_publish PASSED
tests/isolation/test_schema_apis.py::test_editor_cannot_create_or_publish_schemas PASSED
tests/isolation/test_schema_apis.py::test_cross_tenant_schema_access_returns_404 PASSED
tests/isolation/test_schema_apis.py::test_breaking_change_without_flag_rejected PASSED
tests/unit/test_csv_exporter.py::test_export_records_to_s3_deterministic_key PASSED
tests/unit/test_webhook_dispatcher.py::test_ssrf_protection_rejects_private_and_metadata_ips PASSED
tests/unit/test_webhook_dispatcher.py::test_anti_replay_hmac_signature PASSED
tests/unit/test_webhook_dispatcher.py::test_webhook_payload_masks_sensitive_fields_for_viewer PASSED
tests/concurrency/test_idempotency_concurrency.py::test_50_concurrent_identical_requests_produce_one_record PASSED
tests/concurrency/test_idempotency_concurrency.py::test_idempotency_key_payload_mismatch_returns_422 PASSED
tests/concurrency/test_idempotency_concurrency.py::test_principal_scoping_prevents_cross_user_key_clashes PASSED
tests/concurrency/test_workflow_guards.py::test_viewer_forbidden_from_submitting PASSED
tests/concurrency/test_workflow_guards.py::test_condition_guard_rejects_amount_over_limit PASSED
tests/concurrency/test_workflow_guards.py::test_valid_transition_and_audit_outbox_emission PASSED
tests/concurrency/test_workflow_guards.py::test_available_transitions_endpoint PASSED
tests/concurrency/test_workflow_guards.py::test_concurrent_approve_and_reject_race PASSED
tests/isolation/test_ast_repository_enforcement.py::test_ast_forbids_direct_collection_access PASSED
tests/unit/test_repository.py::test_repository_requires_tenant_id PASSED
tests/unit/test_repository.py::test_insert_one_enforces_tenant_id PASSED
tests/unit/test_repository.py::test_insert_one_rejects_mismatched_tenant_id PASSED
tests/unit/test_repository.py::test_find_one_strict_tenant_isolation PASSED
tests/unit/test_repository.py::test_find_excludes_soft_deleted PASSED
tests/unit/test_repository.py::test_aggregate_injects_tenant_match PASSED
tests/unit/test_schemas.py::test_re2_redos_protection PASSED
tests/unit/test_schemas.py::test_json_schema_compiler_all_7_types PASSED
tests/unit/test_schemas.py::test_compatibility_checker_rules PASSED
tests/unit/test_unique_keys.py::test_dynamic_uniqueness_engine PASSED

============================= 41 passed in 14.16s =============================
```

---

## 5. Architecture Decision Records (ADRs)

All architectural decisions are documented with context, decision drivers, and consequences:

1. [ADR 001: Hybrid PostgreSQL & MongoDB Architecture](docs/adr/001-hybrid-postgres-mongo-django-control-plane.md)
2. [ADR 002: Transactional Outbox with SQS vs Change Data Capture (CDC)](docs/adr/002-transactional-outbox-sqs-vs-cdc.md)
3. [ADR 003: ETag Optimistic Concurrency Control vs Pessimistic Locking](docs/adr/003-etag-concurrency-vs-pessimistic-locking.md)
4. [ADR 004: Tree-Walking AST Rule Evaluation vs Python Eval](docs/adr/004-ast-rule-evaluation-vs-eval.md)
5. [ADR 005: Thin Events vs Fat Payloads in Outbox](docs/adr/005-thin-events-vs-fat-outbox-payloads.md)

---

## 6. Quickstart Guide

### Prerequisites
- Python 3.11+
- Node.js 20+
- Docker & Docker Compose

### 1. Launch Infrastructure
```bash
# Start PostgreSQL 16, MongoDB 7.0 Replica Set rs0, and LocalStack (SQS/S3)
make up
```

### 2. Backend Setup
```bash
# Create Python virtual environment and install dependencies
cd apps/api
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Run migrations and start API server
python manage.py migrate
python manage.py runserver 0.0.0.0:8000
```

### 3. Frontend & Studio UI Setup
```bash
# Install workspace dependencies and start Vite dev server
npm install
npm run build
npm run dev --workspace=forge-web-studio
```
Open [http://localhost:3000](http://localhost:3000) to access the **Forge Studio** with live Schema Builder, Mermaid state graph visualizer, and Dynamic Record Explorer.

### 4. Run Test Suite
```bash
make test
```

### 5. High-Concurrency Performance Benchmark
```bash
# Run k6 load test simulating 50 concurrent virtual users
make load-test
```
