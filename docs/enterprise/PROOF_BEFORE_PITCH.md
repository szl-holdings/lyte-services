# Proof before pitch

Lyte may be described as fully operational only when all of these independent
states close for the same source revision:

- **BUILT:** install, compile, lint, migration, container, and static bundles.
- **TESTED:** formulas, governance, auth denial, tenant isolation, connector
  bounds, API contracts, sample scenario, accessibility, responsive overflow,
  and container smoke.
- **MERGED:** the exact candidate passes required protected-main checks.
- **DEPLOYED:** the protected single writer publishes one attested artifact
  closure derived from that merged revision.
- **LIVE-VERIFIED:** provider revision, build identity, source identity,
  readiness, required routes, persistence, connector behavior, receipts, and
  interactive product flow are directly observed.

Anything else is labeled `BLOCKED`, `ROADMAP`, or `UNAVAILABLE` with the exact
failed gate. Provider `RUNNING`, HTTP 200, and a green product tile are never a
substitute for the complete chain.
