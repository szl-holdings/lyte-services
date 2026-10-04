---
title: Lyte Enterprise Signal Lattice
emoji: 🔷
colorFrom: green
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
license: apache-2.0
suggested_hardware: cpu-basic
short_description: Governed observability for services, journeys, and AI agents
tags:
  - observability
  - business-observability
  - opentelemetry
  - ai-agents
  - sre
  - governed-ai
---

# Lyte Enterprise Signal Lattice

**See what is changing. Know what it costs. Act with proof.**

Lyte connects service reliability, customer journeys, AI-agent operations,
delivery, cost, revenue, service impact, and risk to an auditable evidence and
decision chain. The public product includes a deterministic `SAMPLE / MODELED`
enterprise scenario; real-data mode never silently substitutes sample data.

## What is operational

- Service, journey, agent, incident, decision, outcome, and evidence views over
  tenant/workspace-scoped append-only PostgreSQL or local SQLite persistence.
- Explicit formulas with units, input truth labels, evidence references, and
  `UNAVAILABLE` results when required inputs are absent.
- A bounded OpenTelemetry JSON subset, signed governed events, and a read-only,
  repository-allowlisted GitHub Actions connector.
- Durable idempotency, hash-chained receipts, replay protection, Second Brain,
  Living Anatomy traces, deterministic Ask Lyte, and Hatun review.
- An original five-scene Signal Lattice with keyboard navigation, text/table
  alternatives, responsive layouts, reduced-motion, and forced-colors support.
- Exact source identity, health/readiness, Prometheus metrics, migration gates,
  a non-root container, and a vendor-neutral open benchmark harness.

## Run locally

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install --requirement requirements-test.txt --editable .
export LYTE_ENV=development
export LYTE_DEMO_MODE=true
uvicorn lyte.app:app --host 127.0.0.1 --port 7860
python -m pytest
```

Windows PowerShell activation is `.venv\\Scripts\\Activate.ps1`; use `$env:`
for environment variables. Open `http://127.0.0.1:7860` for the Signal Lattice
or `/docs` for the OpenAPI surface.

Build the exact checked-out revision:

```bash
revision="$(git rev-parse HEAD)"
docker build --build-arg "LYTE_SOURCE_REVISION=${revision}" -t "szl-lyte:${revision}" .
docker run --rm -p 7860:7860 -v lyte-data:/data "szl-lyte:${revision}"
```

The image stamps the exact 40-character source revision, generates a canonical
`/app/build-receipt.json` over the closed application payload, and runs with
`LYTE_REQUIRE_BUILD_RECEIPT=true`. Runtime readiness rehashes that payload; a
missing, malformed, stale, or source-mismatched receipt returns HTTP 503. The
receipt covers application files copied by the Dockerfile, not installed
dependency bytes or the entire OCI image. Publication evidence must separately
bind its receipt digest and provider commit to the admitted source revision.

Production requires `DATABASE_URL` with PostgreSQL plus `OIDC_ISSUER`,
`OIDC_AUDIENCE`, and `OIDC_JWKS_URL`. Run `alembic upgrade head` before the
application and set `LYTE_ENV=production`, `LYTE_DEMO_MODE=false`, and
`LYTE_DEV_AUTH_ENABLED=false`. The application then refuses SQLite, incomplete
OIDC configuration, developer credentials, implicit demo data, and unbound
deployment source identity.

Authenticated local mutations are available only when
`LYTE_DEV_AUTH_ENABLED=true` is paired with an explicit 32-character or longer
token plus `LYTE_DEV_TENANT_ID` and `LYTE_DEV_WORKSPACE_ID`. This mode is
forbidden in production.

## Public API

Operations and proof:

- `/healthz` for process liveness
- `/readyz` for fail-closed source, payload-receipt, route, database, and schema readiness
- `/api/live` for application-owned live attestation
- `/build-receipt.json` for the exact immutable application payload receipt
- `/metrics` and `/api/lyte/v2/metrics` for the application metrics registry
- `/api/build-info`, `/.well-known/szl-source.json`

Lyte v2:

- `/api/lyte/v2/catalog`, `/capabilities`, `/anatomy`, `/formulas`, `/sources`
- governed GitHub Actions, OTLP JSON subset, and signed-event ingestion
- services, journeys, outcomes, agents, incidents, decisions, and playback
- deterministic Ask Lyte, tenant-scoped Second Brain, receipts, and Hatun review
- administrator-only exact-scope provisioning with an atomic creation receipt

The fixed public `SAMPLE` scope is read-only. Every durable mutation requires a
verified tenant/workspace principal and an operator or administrator role.

## Authority and truth boundary

Hatun can return only `REVIEW`, `ABSTAIN`, or `DENY`. It cannot authorize or
execute. Formula output is advisory, Lambda remains **Conjecture 1**, production
effectors are disabled, and correlation is not causation. Raw session tokens,
bearer credentials, prompts, responses, and known sensitive telemetry values are
not persisted.

Each product value remains labeled `MEASURED`, `REPORTED`, `MODELED`, `SAMPLE`,
`ROADMAP`, or `UNAVAILABLE`. Benchmark conclusions use a separate controlled
label set and do not claim competitive superiority without like-for-like data.

Canonical source: `szl-holdings/lyte-services`. Canonical public surface:
[`SZLHOLDINGS/lyte`](https://huggingface.co/spaces/SZLHOLDINGS/lyte). Publication
flows only through the protected A11oy single writer from an exact merged source
revision; provider readiness alone is not product-operational proof.

See [`docs/enterprise/ARCHITECTURE.md`](docs/enterprise/ARCHITECTURE.md),
[`docs/enterprise/DEPLOYMENT.md`](docs/enterprise/DEPLOYMENT.md), and
[`docs/enterprise/PROOF_BEFORE_PITCH.md`](docs/enterprise/PROOF_BEFORE_PITCH.md).
