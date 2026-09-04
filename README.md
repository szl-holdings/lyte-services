---
title: Lyte Enterprise Signal Lattice
emoji: ✦
colorFrom: teal
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
license: apache-2.0
short_description: Governed business observability across services, journeys, AI agents, and outcomes
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

Lyte connects software reliability, customer and operating journeys, AI-agent behavior, delivery events, cost, revenue, service, risk, human decisions, and verified outcomes in one governed operating state.

It is built for digitally dependent upper-mid-market and enterprise organizations that already have monitoring, APM, logging, ticketing, CRM, ERP, cloud, or BI systems but cannot reliably connect technical change to business consequence.

## What is operational

- Six Lyte lenses: Signal, Impact, Anticipation, Topology, Posture, and Velocity.
- Service SLI/SLO, error-budget burn, p50/p95/p99 latency, cost per success, dependency graphs, delivery context, and human-owned priority queues.
- Declared customer-journey stages, completion gaps, and explicitly modeled value at risk.
- AI-agent success, latency, token, tool, cost, safety, and privacy summaries without retaining prompt or response content.
- Bounded OpenTelemetry-shaped JSON normalization. This is intentionally not advertised as a wire-compatible OTLP collector.
- Bounded ATLAS business-event normalization.
- Read-only GitHub Actions observation for an allowlisted SZL repository set.
- SQLite-backed append-only Second-Brain receipts scoped by SHA-256 of a caller-held session token.
- Deterministic Ask Lyte answers over the caller's evidence.
- Hatun `REVIEW`, `ABSTAIN`, and `DENY` results with no execution authority.
- Incident playback, source identity, health, readiness, Prometheus-compatible metrics, and a responsive interactive Signal Lattice.

## Product boundary

Lyte does not infer causal proof from correlation. It does not execute unattended remediation. It does not retain raw session tokens, credentials, prompts, responses, or blocked sensitive span attributes.

Lambda remains **Conjecture 1** and advisory only. A formula cannot authorize an action or become the sole basis for an allow decision.

## Run locally

```bash
python -m pip install -r requirements.txt -r requirements-test.txt
export LYTE_SOURCE_REVISION=0000000000000000000000000000000000000000
export LYTE_STATE_PATH=/tmp/lyte-enterprise.sqlite3
python -m pytest tests -q
uvicorn space.server:app --host 0.0.0.0 --port 7860
```

Container:

```bash
docker build \
  --build-arg LYTE_SOURCE_REVISION="$(git rev-parse HEAD)" \
  -t lyte-enterprise .
docker run --rm -p 7860:7860 lyte-enterprise
```

## Public API

```text
GET  /
GET  /healthz
GET  /readyz
GET  /api/build-info
GET  /.well-known/szl-source.json
GET  /metrics

GET  /api/lyte/v3/catalog
GET  /api/lyte/v3/capabilities
GET  /api/lyte/v3/anatomy
GET  /api/lyte/v3/formulas
GET  /api/lyte/v3/sources
GET  /api/lyte/v3/scenario
GET  /api/lyte/v3/github/{repository}
POST /api/lyte/v3/analyze
POST /api/lyte/v3/journeys/analyze
POST /api/lyte/v3/telemetry/otel
POST /api/lyte/v3/telemetry/atlas
GET  /api/lyte/v3/second-brain
GET  /api/lyte/v3/playback
GET  /api/lyte/v3/receipts/{receipt_id}
POST /api/lyte/v3/ask
POST /api/lyte/v3/hatun/evaluate
```

Stateful routes require a 32–128 character high-entropy `X-SZL-Session` header. The raw value is never recorded.

## Deterministic demonstration

`GET /api/lyte/v3/scenario` returns an explicit `SAMPLE` checkout-degradation scenario spanning:

- checkout and payment services;
- an AI support agent;
- customer-journey stages;
- modeled revenue at risk;
- an OpenTelemetry-shaped trace;
- an ATLAS business-risk event;
- playback frames;
- a simulated rollback request that is **not executed**.

The front end uses the same runtime functions as the API. It does not silently replace failed live data with sample data.

## Living Anatomy

```text
Sense → Normalize → Context → Formula → Policy → Decide → Verify → Remember → Receipt
```

Each observation is truth-labelled as `MEASURED`, `REPORTED`, `MODELED`, `SAMPLE`, `ROADMAP`, or `UNAVAILABLE`.

## Enterprise deployment

For production, provide:

- an exact `LYTE_SOURCE_REVISION` or `SZL_SOURCE_REVISION`;
- a writable `LYTE_STATE_PATH` or durable mounted volume;
- an optional read-only `GITHUB_READ_TOKEN` for higher GitHub API limits;
- a reverse proxy or platform identity layer appropriate to the deployment.

The current public runtime uses session-scoped SQLite receipts. Organization-wide multi-tenant identity and PostgreSQL remain a separate release gate and are not claimed by this build.

## Canonical estate role

Lyte is the SZL business-observability product. It consumes the shared formula, receipt, Living Anatomy, Second-Brain, and Hatun governance family without becoming a second A11oy orchestration authority.

Source: `szl-holdings/lyte-services`  
Public Space: `SZLHOLDINGS/lyte`

Apache-2.0. See `NOTICE`.
