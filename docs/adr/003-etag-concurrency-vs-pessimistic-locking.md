# ADR 003: ETag Optimistic Concurrency Control vs Pessimistic Locking

## Status
Accepted

## Context
In a multi-tenant platform where multiple users, background automations, and third-party integrations mutate the same custom object records concurrently, lost updates (where transaction B silently overwrites transaction A's changes) are a critical data corruption risk.

We evaluated two concurrency strategies:
1. **Pessimistic Locking**: Acquiring an exclusive row or document lock upon opening a record for edit, holding the lock across user think-time or API transactions.
2. **Optimistic Concurrency Control (OCC) via HTTP ETags**: Storing an incrementing integer `version` on every record document, emitting `ETag: W/"<version>"`, and requiring `If-Match: W/"<version>"` on all `PATCH` and `DELETE` requests.

## Decision
We chose **Optimistic Concurrency Control (OCC) via HTTP ETags and Version Fields**.

1. **Stateless Web Scaling**: Pessimistic locks require distributed state, heartbeat monitoring, and complex lock expiration to handle network drops and browser closes. OCC is completely stateless; any API worker can validate an update without holding database locks.
2. **RFC 7232 & RFC 6585 Compliance**:
   - `GET /api/v1/o/{type}/{id}/` returns `ETag: W/"<version>"`.
   - `PATCH` or `DELETE` without `If-Match` is rejected immediately with `428 Precondition Required`.
   - If the incoming version does not match the current record version, the API returns `412 Precondition Failed` without executing mutations.
3. **Compare-And-Set Atomicity**: Updates perform an atomic MongoDB query:
   ```json
   {
     "_id": "<record_id>",
     "tenant_id": "<tenant_id>",
     "version": "<expected_version>"
   }
   ```
   with `$inc: { "version": 1 }`. If another request modified the document concurrently, the match count is 0, raising `OptimisticLockError` and returning 412.

## Consequences
- **Positive**:
  - Zero lock contention or deadlocks in high-throughput write workloads.
  - Horizontally scalable across arbitrary numbers of API instances.
  - Standard HTTP status codes give frontend clients clear signals to prompt user resolution or refetch latest state.
- **Negative**:
  - High contention on the exact same record results in retries for the losing client. However, custom object record updates are typically user-driven or bounded, making write conflicts rare and easily handled.
