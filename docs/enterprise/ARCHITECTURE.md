# Lyte Enterprise architecture

Lyte is a Python 3.12 FastAPI product with four deliberately separate planes:

1. **Acquisition** accepts only bounded, schema-checked observations from the
   code-defined GitHub connector, a documented OTLP JSON subset, or the governed
   webhook. No caller can supply an arbitrary fetch URL.
2. **Operating intelligence** normalizes signals into service, journey,
   business, AI-agent, delivery, and decision lenses. Correlation remains
   explicitly non-causal unless an approved evidence basis says otherwise.
3. **Governance and memory** runs the nine-stage Living Anatomy, persists
   tenant/workspace-scoped summaries and append-only receipts, and constrains
   Hatun to review/abstain/deny. No effector exists in the public runtime.
4. **Experience and proof** serves the interactive Signal Lattice, OpenAPI,
   Prometheus metrics, health/readiness, and immutable source/build identity.

SQLite is a file-backed local/demo database. Production points SQLAlchemy and
Alembic at PostgreSQL through `DATABASE_URL`. Every application repository
method takes a tenant/workspace scope; unscoped data access is not exposed.

The deterministic public demo is seeded only when `LYTE_DEMO_MODE=true` and is
always labeled `SAMPLE` or `MODELED`. With demo mode disabled, absent sources
remain `UNAVAILABLE`; there is no sample fallback.

## Source and release chain

```text
lyte-services exact protected main SHA
  -> full CI and container proof
  -> protected a11oy single-writer admission
  -> one atomic Hugging Face artifact closure
  -> HF revision readback
  -> live build/source identity agreement
  -> readiness + required route + UI-flow probes
```

Each arrow is a separate evidence state. A green workflow or HTTP 200 cannot
substitute for the later observations.
