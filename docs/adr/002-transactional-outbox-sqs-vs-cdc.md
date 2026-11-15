# ADR 002: Transactional Outbox Relay with Standard SQS vs MongoDB Change Streams (CDC)

## Status
Accepted

## Context
When tenants perform mutations on dynamic custom objects (create, update, delete, state transitions), downstream asynchronous consumers—such as audit loggers, webhook dispatchers, and S3 batch exporters—must receive reliable, ordered event notifications.

We evaluated two architectural patterns for event publishing:
1. **Change Data Capture (CDC) via MongoDB Change Streams**: Tailing MongoDB's internal replica set oplog to detect document modifications.
2. **Transactional Outbox Pattern**: Committing a discrete thin outbox event document in MongoDB inside the identical multi-document transaction as the record mutation, polled by a leased relay worker and published to AWS Standard SQS.

## Decision
We chose the **Transactional Outbox Pattern with a Leased Relay Worker and AWS Standard SQS**.

1. **Atomic Dual-Write Guarantee**: In MongoDB 7.0+, multi-document transactions ensure that a record mutation and its corresponding outbox row are committed atomically. A crash before commit leaves zero state; a crash after commit ensures the event is safely recorded in the `outbox` collection.
2. **Decoupling from Cluster Oplog Privileges**: CDC requires elevated cluster administration privileges (`changeStream` and oplog read permissions), which is frequently restricted in multi-tenant shared database environments or managed cloud services (e.g. Atlas shared clusters, DocumentDB).
3. **Event Schema Control & Thin Payloads**: Change streams emit raw BSON oplog diffs that risk leaking unmasked PII or internal collection details. The outbox pattern allows us to emit strictly controlled, thin domain events (`event_id`, `tenant_id`, `topic`, `action`, `record_id`, `version`, `tombstone`).
4. **Relay Lease Resiliency**: The relay worker uses an atomic claim filter:
   ```json
   {
     "$or": [
       { "status": "pending" },
       { "status": "in_flight", "locked_until": { "$lt": "<now>" } }
     ]
   }
   ```
   This prevents stranding events if a worker crashes mid-batch.
5. **Idempotent Consumers with Deduplication Ledger**: Standard SQS provides at-least-once delivery with virtually unlimited throughput. Consumers maintain a unique MongoDB deduplication ledger on `(tenant_id, event_id, consumer_name)`, guaranteeing zero duplicate side effects.

## Consequences
- **Positive**:
  - Zero lost events across any worker failure mode.
  - Zero operational dependencies on MongoDB oplog internals.
  - Native integration with AWS SQS Dead Letter Queues (DLQ).
  - Outbox rows are automatically purged after 7 days via a MongoDB TTL index on `published_at`.
- **Negative**:
  - Adds a small polling delay (100–500ms) compared to instantaneous oplog tailing.
  - Requires maintaining the relay worker process alongside the API workers.

