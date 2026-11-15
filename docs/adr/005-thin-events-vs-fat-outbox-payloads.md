# ADR 005: Thin Events vs Fat Payloads in Outbox & Asynchronous Pipelines

## Status
Accepted

## Context
When an object record is modified, the system records an outbox event that is published to SQS and distributed to downstream workers (audit loggers, webhook dispatchers, S3 exporters).

We considered two event payload models:
1. **Fat Payloads**: Storing the full serialized document data (`data: { ... }`) directly in the outbox collection and SQS message body.
2. **Thin Events**: Storing only event identifiers, metadata, and altered field keys (`event_id`, `record_id`, `type`, `version`, `action`, `changed_fields`, `tombstone`), requiring consumers to fetch record data under their own permission profile.

## Decision
We chose **Thin Events in the Outbox and Messaging Pipeline**.

1. **Elimination of PII / Secret Data Leakage**:
   - In a multi-tenant platform with field-level RBAC (e.g. sensitive financial or PII fields configured as `hidden` or `masked` for certain roles or subscribers), storing full record data in the outbox creates an unmasked copy of sensitive data in MongoDB and SQS queues.
   - Any worker or subscriber with queue access could bypass application-layer field masking.
2. **Dynamic Permission Evaluation at Delivery Time**:
   - Webhook endpoints register with specific role profiles (`admin`, `editor`, `viewer`).
   - When the `WebhookDispatcherConsumer` receives a thin event, it fetches the current record state from MongoDB using `TenantScopedRepository` and executes `FieldPermissionManager.mask_record_data()` tailored to that endpoint's configured role.
   - A webhook registered by a `Viewer` receives only permitted fields, completely stripping `hidden` fields and obfuscating `masked` fields.
3. **Message Size Limits**: AWS Standard SQS limits message size to 256 KB. Storing massive fat payloads in outbox events risks hitting queue size limits. Thin events remain compact (typically < 1 KB).

## Consequences
- **Positive**:
  - Zero PII / secret leakage through queue infrastructure.
  - Consistent enforcement of field-level RBAC across both REST APIs and asynchronous webhooks.
  - Compact SQS messages and lightweight outbox collection storage.
- **Negative**:
  - Consumers that need record data must perform a read against MongoDB. Because MongoDB read latency is sub-millisecond on indexed `_id`, this trade-off is well worth the security guarantee.

