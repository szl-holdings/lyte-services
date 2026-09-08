# Connector contracts

| Connector | Mode | Operational proof | Closed boundary |
|---|---|---|---|
| GitHub Actions | Public/read-only | Allowlisted workflow runs with head SHA, branch, event, conclusion, duration, ETag, and receipt | No arbitrary org/repo/URL, redirect, or unbounded pagination |
| OTLP JSON subset | Caller ingest | Validated trace, metric, and log summaries with explicit accepted/rejected counts and receipt | Not a full OTLP collector; unsupported fields fail clearly; no silent truncation |
| Governed event | Caller ingest | Strict source/event schema, raw-body HMAC when configured, timestamp/nonce/idempotency, truth label, and receipt | Unsigned configured sources, replays, bad schema, and stale timestamps fail closed |

Prometheus, Azure Monitor, CloudWatch, Kubernetes, ServiceNow, Jira,
Salesforce, and cloud-cost connectors remain `ROADMAP` until a working adapter,
credentials boundary, fixture, and live source receipt all exist.
