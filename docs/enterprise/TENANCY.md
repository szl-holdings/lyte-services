# Tenancy

The security scope is `(tenant_id, workspace_id)`. IDs are stable UUIDs and are
included in database uniqueness constraints, indexes, idempotency records,
memory, replay, evidence, and receipts.

Roles are `viewer`, `operator`, `analyst`, `admin`, and `auditor`. Viewers can
read scoped state; analysts can query and model; operators can ingest; admins
manage scoped sources; auditors read evidence and receipts. None can enable an
effector through this release.

The public demo uses a dedicated, immutable sample scope. Production identities
obtain scope from verified JWT claims. Repository methods do not expose an
unscoped list or lookup, and overlapping entity names across tenants must remain
isolated in API, Ask Lyte, playback, Second Brain, and receipt reads.
