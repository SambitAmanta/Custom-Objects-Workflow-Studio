# ADR 001: Hybrid Relational & Document Storage (Why Django if records bypass the ORM?)

## Status
Accepted

## Context
Forge is a multi-tenant platform where organizations define custom object schemas, field validations, workflows, and permissions at runtime. The system has two distinct data persistence requirements:

1. **Control Plane (Identity & Tenancy):** Users, tenants, memberships, roles, API keys, and webhook registrations require strict relational integrity, foreign key cascading constraints, and battle-tested authentication pipelines. Django's auth ecosystem and ORM migrations provide mature solutions for these requirements.
2. **Data Plane (Dynamic Custom Objects):** Tenant-defined objects have arbitrary, evolving schemas with dynamic field types. Representing dynamic custom objects in a static relational database introduces significant drawbacks:
   - **Entity-Attribute-Value (EAV):** Requires multiple SQL joins per query, leading to severe query degradation.
   - **Runtime DDL (`ALTER TABLE`):** Dynamic schema changes executing DDL at scale risk lock contention and table-level locks.
   - **Postgres `JSONB`:** While flexible, dynamic indexing across arbitrary user-defined keys incurs significant overhead, and multi-tenant schema isolation is cumbersome.

## Decision
We adopt a **hybrid persistence architecture**:
- **PostgreSQL 16 (via Django ORM):** Manages the control plane (`User`, `Tenant`, `TenantMembership`, `ApiKey`, `WebhookEndpoint`). All authentication, session management, and RBAC membership resolution leverage Django's ORM and migration machinery.
- **MongoDB 7.0+ (via PyMongo wrapped in `TenantScopedRepository`):** Manages the data plane (`records`, `unique_keys`, `object_types`, `outbox`, and `audit_events`). Records bypass the Django ORM by design and are accessed strictly through `TenantScopedRepository`. Multi-document ACID transactions (`session.with_transaction()`) guarantee atomic commits across records, dynamic uniqueness entries, audit events, and outbox rows.

## Consequences

### Positive
- **No Schema Migration Friction:** Tenants can create, update, and publish custom object schemas instantaneously without executing dangerous runtime DDL.
- **Battle-Tested Control Plane:** We leverage Django REST Framework's authentication, permission pipelines, throttling, and middleware architecture.
- **Strict Tenant Isolation:** All MongoDB queries are mediated through `TenantScopedRepository`, which automatically injects `tenant_id` and filters soft-deleted documents (`deleted_at: None`).
- **Atomic Multi-Document Consistency:** MongoDB 7.0+ replica set transactions guarantee that record mutations, dynamic uniqueness checks (`unique_keys`), outbox events, and audit logs commit or roll back together.

### Negative & Mitigations
- **Dual Persistence Infrastructure:** Two database engines (Postgres and MongoDB) must be maintained in production and local environments. *Mitigation:* Managed cloud instances (RDS/Aurora and MongoDB Atlas) alongside containerized Docker Compose configurations for local development.
- **Cross-Engine Referential Integrity:** Foreign keys cannot span between Postgres (users) and Mongo (records). *Mitigation:* Enforced at the application and serializer layer inside Django views and `TenantScopedRepository`.

