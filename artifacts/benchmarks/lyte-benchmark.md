# Lyte open observability benchmark

This report measures Lyte against committed open acceptance gates. It did not exercise a competitor, scrape a proprietary service, or infer superiority from features or visual opinion.

## Provenance

| Field | Value |
|---|---|
| Run ID | `b633f746e558da4b32d117dfa7d8bb74c24e2b25859f08a8694106a8e17e6776` |
| Generated | `2026-09-08T04:25:20.379371Z` |
| Source revision | `f7b6d012c83c2c5fcbe83565bd999af860f5280f` |
| Working tree | `clean` |
| Fixture | `checkout-observability-v1@1.0.0` (`CC0-1.0`) |
| Fixture SHA-256 | `bf35189a0845adb7ba8d5fe2c6599432debfa10a47ff0a52aea64e798a50ef59` |
| Target SHA-256 | `459f77b23daf658082a280fbef16d542e655b5cebf0a5598c2fda4c5398c05de` |
| Exact command | `'C:\Users\steph\Documents\Codex\2026-09-04\get-this-fuully-oeprational-and-real\work\lyte-services\.venv\Scripts\python.exe' -m benchmarks.observability.run_benchmark --source-revision f7b6d012c83c2c5fcbe83565bd999af860f5280f --working-tree-state clean --browser-evidence 'artifacts\benchmarks\browser\lyte-ui-responsive.json' --output-dir 'artifacts\benchmarks'` |

## Results

`MEASURED_PARITY` means a committed open gate was met; it is not parity with an external vendor. `MEASURED_GAP` means the gate was missed. Browser evidence that was not collected remains `NOT_TESTED` with no substitute zero.

| Dimension | Result label | Measurement | Predeclared gate |
|---|---|---:|---|
| Time to first source | `MEASURED_PARITY` | 1427.3603 ms | <= 2000.0 ms (p95) |
| Time to first useful service view | `MEASURED_PARITY` | 1446.2307 ms | <= 2500.0 ms (p95) |
| Time to map a service to a business outcome | `MEASURED_PARITY` | 19.9144 ms | <= 1000.0 ms (p95) |
| Time to explain an incident with evidence | `MEASURED_PARITY` | 17.1948 ms | <= 1000.0 ms (p95) |
| Time to replay a decision | `MEASURED_PARITY` | 9.2661 ms | <= 1000.0 ms (p95) |
| Receipt completeness | `MEASURED_PARITY` | 100.0 percent | >= 100.0 percent (coverage) |
| Truth-label completeness | `MEASURED_PARITY` | 100.0 percent | >= 100.0 percent (coverage) |
| Source-binding completeness | `MEASURED_PARITY` | 100.0 percent | >= 100.0 percent (coverage) |
| Query latency | `MEASURED_PARITY` | 5.3242 ms | <= 100.0 ms (p95) |
| Ingest throughput | `MEASURED_PARITY` | 675.441553 records/s | >= 100.0 records/s (rate) |
| Storage growth | `MEASURED_PARITY` | 2068.48 bytes/record | <= 32768.0 bytes/record (rate) |
| UI keyboard completion | `MEASURED_PARITY` | 1.0 completed | = 1.0 completed (all_declared_sequences) |
| Mobile overflow | `MEASURED_PARITY` | 0.0 overflow_px | = 0.0 overflow_px (maximum) |
| Bundle size | `MEASURED_PARITY` | 29353 gzip_bytes | <= 300000.0 gzip_bytes (total) |
| Resource consumption | `MEASURED_PARITY` | peak RSS 91734016 bytes; CPU 1.28125 s; wall 2.7540482 s | all <= {"cpu_time_seconds": 20.0, "peak_rss_bytes": 536870912.0, "wall_time_seconds": 60.0} |

## Raw evidence and limitations

### Time to first source

Result: `MEASURED_PARITY`. Measurement: 1427.3603 ms.

Evidence: `benchmarks/observability/fixtures/checkout-observability-v1.json`; `lyte/demo.py`; `lyte/api/routes_entities.py`; `lyte/api/routes_ask.py`.

Raw measurements:

```json
{
  "elapsed_ms": [
    1427.3603,
    133.0542,
    97.272,
    93.075,
    93.5843
  ]
}
```

Limitations:

- In-process FastAPI TestClient and local SQLite are measured; network, proxy, and PostgreSQL costs are excluded.
- The dataset is the public synthetic SAMPLE fixture, not production telemetry.

### Time to first useful service view

Result: `MEASURED_PARITY`. Measurement: 1446.2307 ms.

Evidence: `benchmarks/observability/fixtures/checkout-observability-v1.json`; `lyte/demo.py`; `lyte/api/routes_entities.py`; `lyte/api/routes_ask.py`.

Raw measurements:

```json
{
  "elapsed_ms": [
    1446.2307,
    197.433,
    117.1762,
    111.2884,
    110.4427
  ]
}
```

Limitations:

- In-process FastAPI TestClient and local SQLite are measured; network, proxy, and PostgreSQL costs are excluded.
- The dataset is the public synthetic SAMPLE fixture, not production telemetry.

### Time to map a service to a business outcome

Result: `MEASURED_PARITY`. Measurement: 19.9144 ms.

Evidence: `benchmarks/observability/fixtures/checkout-observability-v1.json`; `lyte/demo.py`; `lyte/api/routes_entities.py`; `lyte/api/routes_ask.py`.

Raw measurements:

```json
{
  "elapsed_ms": [
    19.9144,
    12.0947,
    8.3822,
    8.639,
    9.022
  ]
}
```

Limitations:

- In-process FastAPI TestClient and local SQLite are measured; network, proxy, and PostgreSQL costs are excluded.
- The dataset is the public synthetic SAMPLE fixture, not production telemetry.

### Time to explain an incident with evidence

Result: `MEASURED_PARITY`. Measurement: 17.1948 ms.

Evidence: `benchmarks/observability/fixtures/checkout-observability-v1.json`; `lyte/demo.py`; `lyte/api/routes_entities.py`; `lyte/api/routes_ask.py`.

Raw measurements:

```json
{
  "elapsed_ms": [
    17.1948,
    10.7874,
    9.1099,
    9.3987,
    9.9888
  ]
}
```

Limitations:

- In-process FastAPI TestClient and local SQLite are measured; network, proxy, and PostgreSQL costs are excluded.
- The dataset is the public synthetic SAMPLE fixture, not production telemetry.

### Time to replay a decision

Result: `MEASURED_PARITY`. Measurement: 9.2661 ms.

Evidence: `benchmarks/observability/fixtures/checkout-observability-v1.json`; `lyte/demo.py`; `lyte/api/routes_entities.py`; `lyte/api/routes_ask.py`.

Raw measurements:

```json
{
  "elapsed_ms": [
    9.1431,
    9.2661,
    8.4689,
    7.097,
    7.6433
  ]
}
```

Limitations:

- In-process FastAPI TestClient and local SQLite are measured; network, proxy, and PostgreSQL costs are excluded.
- The dataset is the public synthetic SAMPLE fixture, not production telemetry.

### Receipt completeness

Result: `MEASURED_PARITY`. Measurement: 100.0 percent.

Evidence: `lyte/api/responses.py`; `lyte/api/routes_health.py`; `lyte/domain/receipts.py`; `runtime API responses from the open fixture`.

Raw measurements:

```json
{
  "append_only_declared": true,
  "chain_head": "d7b6245c6ee88fab34767dd7c13a160579277ee090f271120be3f4d009f6a5b5",
  "checks_per_receipt": 14,
  "denominator": 294,
  "missing_or_invalid": [],
  "numerator": 294,
  "percentage": 100.0,
  "receipt_count": 21
}
```

Limitations:

- In-process FastAPI TestClient and local SQLite are measured; network, proxy, and PostgreSQL costs are excluded.
- The dataset is the public synthetic SAMPLE fixture, not production telemetry.
- artifacts are evaluated after ingest

### Truth-label completeness

Result: `MEASURED_PARITY`. Measurement: 100.0 percent.

Evidence: `lyte/api/responses.py`; `lyte/api/routes_health.py`; `lyte/domain/receipts.py`; `runtime API responses from the open fixture`.

Raw measurements:

```json
{
  "contract": "explicit response, record, body, indicator, citation, and formula points",
  "denominator": 111,
  "invalid_labels": [],
  "missing_paths": [],
  "numerator": 111,
  "observed_product_truth_labels": {
    "MEASURED": 3,
    "MODELED": 6,
    "REPORTED": 22,
    "ROADMAP": 1,
    "SAMPLE": 78,
    "UNAVAILABLE": 1
  },
  "percentage": 100.0
}
```

Limitations:

- In-process FastAPI TestClient and local SQLite are measured; network, proxy, and PostgreSQL costs are excluded.
- The dataset is the public synthetic SAMPLE fixture, not production telemetry.
- only explicitly enumerated benchmark response contract points are in the denominator

### Source-binding completeness

Result: `MEASURED_PARITY`. Measurement: 100.0 percent.

Evidence: `lyte/api/responses.py`; `lyte/api/routes_health.py`; `lyte/domain/receipts.py`; `runtime API responses from the open fixture`.

Raw measurements:

```json
{
  "checks": {
    "bindings_agree": true,
    "build_repository_canonical": true,
    "build_revision_exact": true,
    "build_state_observed": true,
    "build_truth_measured": true,
    "effectors_disabled": true,
    "evidence_source_present": true,
    "hub_surface_canonical": true,
    "human_approval_required": true,
    "invalid_sources_empty": true,
    "one_distinct_revision": true,
    "product_repository_canonical": true,
    "product_revision_exact": true,
    "runtime_repository_canonical": true,
    "runtime_source_revision_exact": true,
    "source_repository_canonical": true,
    "source_revision_exact": true
  },
  "denominator": 17,
  "expected_revision": "f7b6d012c83c2c5fcbe83565bd999af860f5280f",
  "missing_or_mismatched": [],
  "numerator": 17,
  "observed_revision": "f7b6d012c83c2c5fcbe83565bd999af860f5280f",
  "percentage": 100.0
}
```

Limitations:

- In-process FastAPI TestClient and local SQLite are measured; network, proxy, and PostgreSQL costs are excluded.
- The dataset is the public synthetic SAMPLE fixture, not production telemetry.
- the exact source revision is injected into the isolated test runtime and does not prove a deployed artifact

### Query latency

Result: `MEASURED_PARITY`. Measurement: 5.3242 ms.

Evidence: `lyte/api/routes_entities.py`; `runtime API responses from the open fixture`.

Raw measurements:

```json
{
  "elapsed_ms": [
    3.6625,
    4.013,
    3.2649,
    2.5439,
    3.9997,
    3.7633,
    2.7192,
    2.2513,
    4.2558,
    3.1165,
    2.8557,
    2.1935,
    2.0861,
    2.0032,
    2.4495,
    5.3242,
    3.7357,
    3.3476,
    3.9903,
    5.5114,
    3.0105,
    3.5731,
    3.9921,
    2.5343,
    1.9917,
    3.7369,
    3.4537,
    2.1944,
    2.4416,
    4.7322
  ]
}
```

Limitations:

- In-process FastAPI TestClient and local SQLite are measured; network, proxy, and PostgreSQL costs are excluded.
- The dataset is the public synthetic SAMPLE fixture, not production telemetry.

### Ingest throughput

Result: `MEASURED_PARITY`. Measurement: 675.441553 records/s.

Evidence: `benchmarks/observability/fixtures/checkout-observability-v1.json`; `lyte/connectors/otlp_http.py`; `lyte/api/routes_ingest.py`.

Raw measurements:

```json
{
  "rejected_batches": [],
  "request_elapsed_ms": [
    22.1502,
    13.7127,
    15.8476,
    13.4449,
    13.8289,
    15.6016,
    14.3558,
    14.0038,
    14.0372,
    14.4005,
    13.4923,
    14.726,
    14.7565,
    14.1269,
    13.1024,
    16.0854,
    14.4302,
    14.0774,
    15.1033,
    14.819
  ]
}
```

Limitations:

- In-process FastAPI TestClient and local SQLite are measured; network, proxy, and PostgreSQL costs are excluded.
- The dataset is the public synthetic SAMPLE fixture, not production telemetry.

### Storage growth

Result: `MEASURED_PARITY`. Measurement: 2068.48 bytes/record.

Evidence: `temporary SQLite benchmark database`; `lyte/persistence/models.py`.

Raw measurements:

```json
{
  "accepted_records": 200,
  "baseline_bytes": 286720,
  "delta_bytes": 413696,
  "final_bytes": 700416
}
```

Limitations:

- In-process FastAPI TestClient and local SQLite are measured; network, proxy, and PostgreSQL costs are excluded.
- The dataset is the public synthetic SAMPLE fixture, not production telemetry.
- SQLite page allocation and receipt/projection overhead are included; PostgreSQL storage behavior is not inferred.

### UI keyboard completion

Result: `MEASURED_PARITY`. Measurement: 1.0 completed.

Evidence: `C:\Users\steph\Documents\Codex\2026-09-04\get-this-fuully-oeprational-and-real\work\lyte-services\artifacts\benchmarks\browser\lyte-ui-responsive.json`; `C:/Users/steph/Documents/Codex/2026-09-04/get-this-fuully-oeprational-and-real/work/lyte-services/artifacts/benchmarks/browser/screenshots/lyte-320x568.png`; `C:/Users/steph/Documents/Codex/2026-09-04/get-this-fuully-oeprational-and-real/work/lyte-services/artifacts/benchmarks/browser/screenshots/lyte-375x812.png`; `C:/Users/steph/Documents/Codex/2026-09-04/get-this-fuully-oeprational-and-real/work/lyte-services/artifacts/benchmarks/browser/screenshots/lyte-430x932.png`; `C:/Users/steph/Documents/Codex/2026-09-04/get-this-fuully-oeprational-and-real/work/lyte-services/artifacts/benchmarks/browser/screenshots/lyte-768x1024.png`; `C:/Users/steph/Documents/Codex/2026-09-04/get-this-fuully-oeprational-and-real/work/lyte-services/artifacts/benchmarks/browser/screenshots/lyte-1024x768.png`; `C:/Users/steph/Documents/Codex/2026-09-04/get-this-fuully-oeprational-and-real/work/lyte-services/artifacts/benchmarks/browser/screenshots/lyte-1440x900.png`; `C:/Users/steph/Documents/Codex/2026-09-04/get-this-fuully-oeprational-and-real/work/lyte-services/artifacts/benchmarks/browser/screenshots/lyte-1920x1080.png`.

Raw measurements:

```json
{
  "completed": true,
  "focus_order": [
    {
      "id": null,
      "nodeId": null,
      "sceneTarget": null,
      "tag": "a",
      "text": "Skip to command workspace"
    },
    {
      "id": null,
      "nodeId": null,
      "sceneTarget": null,
      "tag": "a",
      "text": "L Y LYTE Business observability"
    },
    {
      "id": "source-button",
      "nodeId": null,
      "sceneTarget": null,
      "tag": "button",
      "text": "MEASURED No live data source Build f7b6d012c83c"
    },
    {
      "id": "ask-open",
      "nodeId": null,
      "sceneTarget": null,
      "tag": "button",
      "text": "Ask Lyte Ctrl K"
    },
    {
      "id": null,
      "nodeId": null,
      "sceneTarget": "executive",
      "tag": "button",
      "text": "01 Executive Command Outcome posture"
    },
    {
      "id": null,
      "nodeId": null,
      "sceneTarget": "services",
      "tag": "button",
      "text": "02 Service Intelligence System behavior"
    },
    {
      "id": null,
      "nodeId": null,
      "sceneTarget": "journeys",
      "tag": "button",
      "text": "03 Journey Intelligence Customer impact"
    },
    {
      "id": null,
      "nodeId": null,
      "sceneTarget": "agents",
      "tag": "button",
      "text": "04 AI Agent Operations Tools and policy"
    },
    {
      "id": null,
      "nodeId": null,
      "sceneTarget": "incidents",
      "tag": "button",
      "text": "05 Incident Playback Decision timeline"
    },
    {
      "id": null,
      "nodeId": null,
      "sceneTarget": "services",
      "tag": "button",
      "text": "Explore SAMPLE command"
    },
    {
      "id": null,
      "nodeId": null,
      "sceneTarget": null,
      "tag": "button",
      "text": "Connect one source"
    },
    {
      "id": null,
      "nodeId": null,
      "sceneTarget": null,
      "tag": "article",
      "text": "Revenue / currently at risk MODELED $184k + $41k from checkout latency model"
    },
    {
      "id": null,
      "nodeId": null,
      "sceneTarget": null,
      "tag": "article",
      "text": "Cost / AI agent retries MODELED $12.4k + 18% from policy-loop retries"
    },
    {
      "id": null,
      "nodeId": null,
      "sceneTarget": null,
      "tag": "article",
      "text": "Service / critical journey availability SAMPLE 99.82% −0.13 pts against SAMPLE objective"
    },
    {
      "id": null,
      "nodeId": null,
      "sceneTarget": null,
      "tag": "article",
      "text": "Risk / material incidents SAMPLE 03active 1 contained human approval retained"
    },
    {
      "id": null,
      "nodeId": null,
      "sceneTarget": null,
      "tag": "button",
      "text": "All"
    },
    {
      "id": null,
      "nodeId": null,
      "sceneTarget": null,
      "tag": "button",
      "text": "Services"
    },
    {
      "id": null,
      "nodeId": null,
      "sceneTarget": null,
      "tag": "button",
      "text": "Journeys"
    },
    {
      "id": null,
      "nodeId": null,
      "sceneTarget": null,
      "tag": "button",
      "text": "Agents"
    },
    {
      "id": null,
      "nodeId": "gateway",
      "sceneTarget": null,
      "tag": "g",
      "text": "API gateway, service, Elevated latency, truth SAMPLE. Open evidence."
    },
    {
      "id": null,
      "nodeId": "identity",
      "sceneTarget": null,
      "tag": "g",
      "text": "Identity policy, service, Nominal, truth SAMPLE. Open evidence."
    },
    {
      "id": null,
      "nodeId": "checkout",
      "sceneTarget": null,
      "tag": "g",
      "text": "Checkout journey, journey, Degraded, truth MODELED. Open evidence."
    },
    {
      "id": null,
      "nodeId": "support",
      "sceneTarget": null,
      "tag": "g",
      "text": "Support journey, journey, Watching, truth SAMPLE. Open evidence."
    },
    {
      "id": null,
      "nodeId": "policy-agent",
      "sceneTarget": null,
      "tag": "g",
      "text": "Policy agent, agent, Constrained, truth SAMPLE. Open evidence."
    },
    {
      "id": null,
      "nodeId": "resolution-agent",
      "sceneTarget": null,
      "tag": "g",
      "text": "Resolution agent, agent, Retrying, truth SAMPLE. Open evidence."
    }
  ],
  "required_sequence": [
    "Tab through global controls and scene navigation",
    "Enter on every operating lens",
    "ArrowRight between visible Signal Lattice nodes",
    "Enter to inspect focused node",
    "Escape to close evidence drawer",
    "Enter and Escape on Ask Lyte dialog"
  ],
  "steps": [
    {
      "name": "activate all five operating lenses with Enter",
      "observed": {
        "agents": true,
        "executive": true,
        "incidents": true,
        "journeys": true,
        "services": true
      },
      "passed": true
    },
    {
      "name": "move between lattice nodes with ArrowRight",
      "observed": {
        "after": {
          "id": null,
          "nodeId": "checkout",
          "sceneTarget": null,
          "tag": "g",
          "text": "Checkout journey, journey, Degraded, truth MODELED. Open evidence."
        },
        "before": {
          "id": null,
          "nodeId": "gateway",
          "sceneTarget": null,
          "tag": "g",
          "text": "API gateway, service, Elevated latency, truth SAMPLE. Open evidence."
        }
      },
      "passed": true
    },
    {
      "name": "open focused lattice evidence with Enter",
      "observed": {
        "drawerOpen": true
      },
      "passed": true
    },
    {
      "name": "close lattice evidence with Escape",
      "observed": {
        "drawerClosed": true,
        "focus": {
          "id": null,
          "nodeId": null,
          "sceneTarget": null,
          "tag": "body",
          "text": "Skip to command workspace L Y LYTE Business observability Connect system behavior to economic outcome MEASURED No live d"
        }
      },
      "passed": true
    },
    {
      "name": "open Ask Lyte dialog with Enter",
      "observed": {
        "dialogOpen": true
      },
      "passed": true
    },
    {
      "name": "close Ask Lyte dialog with Escape",
      "observed": {
        "dialogClosed": true,
        "focus": {
          "id": "ask-open",
          "nodeId": null,
          "sceneTarget": null,
          "tag": "button",
          "text": "Ask Lyte Ctrl K"
        }
      },
      "passed": true
    },
    {
      "name": "Tab reaches navigation and a global command",
      "observed": {
        "globalCommandReached": true,
        "navigationReached": true
      },
      "passed": true
    }
  ],
  "viewport": {
    "height": 900,
    "width": 1440
  }
}
```

Limitations:

- This run covers one Chromium implementation; Safari and Firefox are not inferred.
- Forced-colors and reduced-motion support remain separately checked by static contract tests unless those OS preferences are active for this browser run.
- Screenshots contain the public SAMPLE / MODELED scenario and no customer telemetry.

### Mobile overflow

Result: `MEASURED_PARITY`. Measurement: 0.0 overflow_px.

Evidence: `C:\Users\steph\Documents\Codex\2026-09-04\get-this-fuully-oeprational-and-real\work\lyte-services\artifacts\benchmarks\browser\lyte-ui-responsive.json`; `C:/Users/steph/Documents/Codex/2026-09-04/get-this-fuully-oeprational-and-real/work/lyte-services/artifacts/benchmarks/browser/screenshots/lyte-320x568.png`; `C:/Users/steph/Documents/Codex/2026-09-04/get-this-fuully-oeprational-and-real/work/lyte-services/artifacts/benchmarks/browser/screenshots/lyte-375x812.png`; `C:/Users/steph/Documents/Codex/2026-09-04/get-this-fuully-oeprational-and-real/work/lyte-services/artifacts/benchmarks/browser/screenshots/lyte-430x932.png`; `C:/Users/steph/Documents/Codex/2026-09-04/get-this-fuully-oeprational-and-real/work/lyte-services/artifacts/benchmarks/browser/screenshots/lyte-768x1024.png`; `C:/Users/steph/Documents/Codex/2026-09-04/get-this-fuully-oeprational-and-real/work/lyte-services/artifacts/benchmarks/browser/screenshots/lyte-1024x768.png`; `C:/Users/steph/Documents/Codex/2026-09-04/get-this-fuully-oeprational-and-real/work/lyte-services/artifacts/benchmarks/browser/screenshots/lyte-1440x900.png`; `C:/Users/steph/Documents/Codex/2026-09-04/get-this-fuully-oeprational-and-real/work/lyte-services/artifacts/benchmarks/browser/screenshots/lyte-1920x1080.png`.

Raw measurements:

```json
{
  "local_assets_only": true,
  "viewports": [
    {
      "activeScene": "executive",
      "bodyClientWidth": 320,
      "bodyScrollWidth": 320,
      "documentClientWidth": 320,
      "documentScrollWidth": 320,
      "externalResources": [],
      "forcedColorsQuery": false,
      "height": 568,
      "horizontalOverflowPx": 0,
      "innerHeight": 568,
      "innerWidth": 320,
      "passed": true,
      "reducedMotionQuery": false,
      "rightmostRenderedPixel": 857.531,
      "screenshot": "C:/Users/steph/Documents/Codex/2026-09-04/get-this-fuully-oeprational-and-real/work/lyte-services/artifacts/benchmarks/browser/screenshots/lyte-320x568.png",
      "url": "http://127.0.0.1:7860/static/lyte/index.html#executive",
      "visibleTruthLabels": [
        "SAMPLE",
        "SAMPLE",
        "MODELED",
        "MODELED",
        "SAMPLE",
        "SAMPLE",
        "SAMPLE",
        "SAMPLE",
        "SAMPLE",
        "MODELED",
        "SAMPLE",
        "SAMPLE",
        "MODELED",
        "MEASURED",
        "REPORTED",
        "SAMPLE",
        "MODELED",
        "UNAVAILABLE"
      ],
      "width": 320
    },
    {
      "activeScene": "executive",
      "bodyClientWidth": 375,
      "bodyScrollWidth": 375,
      "documentClientWidth": 375,
      "documentScrollWidth": 375,
      "externalResources": [],
      "forcedColorsQuery": false,
      "height": 812,
      "horizontalOverflowPx": 0,
      "innerHeight": 812,
      "innerWidth": 375,
      "passed": true,
      "reducedMotionQuery": false,
      "rightmostRenderedPixel": 857.531,
      "screenshot": "C:/Users/steph/Documents/Codex/2026-09-04/get-this-fuully-oeprational-and-real/work/lyte-services/artifacts/benchmarks/browser/screenshots/lyte-375x812.png",
      "url": "http://127.0.0.1:7860/static/lyte/index.html#executive",
      "visibleTruthLabels": [
        "SAMPLE",
        "SAMPLE",
        "MODELED",
        "MODELED",
        "SAMPLE",
        "SAMPLE",
        "SAMPLE",
        "SAMPLE",
        "SAMPLE",
        "MODELED",
        "SAMPLE",
        "SAMPLE",
        "MODELED",
        "MEASURED",
        "REPORTED",
        "SAMPLE",
        "MODELED",
        "UNAVAILABLE"
      ],
      "width": 375
    },
    {
      "activeScene": "executive",
      "bodyClientWidth": 430,
      "bodyScrollWidth": 430,
      "documentClientWidth": 430,
      "documentScrollWidth": 430,
      "externalResources": [],
      "forcedColorsQuery": false,
      "height": 932,
      "horizontalOverflowPx": 0,
      "innerHeight": 932,
      "innerWidth": 430,
      "passed": true,
      "reducedMotionQuery": false,
      "rightmostRenderedPixel": 857.531,
      "screenshot": "C:/Users/steph/Documents/Codex/2026-09-04/get-this-fuully-oeprational-and-real/work/lyte-services/artifacts/benchmarks/browser/screenshots/lyte-430x932.png",
      "url": "http://127.0.0.1:7860/static/lyte/index.html#executive",
      "visibleTruthLabels": [
        "SAMPLE",
        "SAMPLE",
        "MODELED",
        "MODELED",
        "SAMPLE",
        "SAMPLE",
        "SAMPLE",
        "SAMPLE",
        "SAMPLE",
        "MODELED",
        "SAMPLE",
        "SAMPLE",
        "MODELED",
        "MEASURED",
        "REPORTED",
        "SAMPLE",
        "MODELED",
        "UNAVAILABLE"
      ],
      "width": 430
    },
    {
      "activeScene": "executive",
      "bodyClientWidth": 753,
      "bodyScrollWidth": 753,
      "documentClientWidth": 753,
      "documentScrollWidth": 753,
      "externalResources": [],
      "forcedColorsQuery": false,
      "height": 1024,
      "horizontalOverflowPx": 0,
      "innerHeight": 1024,
      "innerWidth": 768,
      "passed": true,
      "reducedMotionQuery": false,
      "rightmostRenderedPixel": 862.5,
      "screenshot": "C:/Users/steph/Documents/Codex/2026-09-04/get-this-fuully-oeprational-and-real/work/lyte-services/artifacts/benchmarks/browser/screenshots/lyte-768x1024.png",
      "url": "http://127.0.0.1:7860/static/lyte/index.html#executive",
      "visibleTruthLabels": [
        "MEASURED",
        "SAMPLE",
        "SAMPLE",
        "MODELED",
        "MODELED",
        "SAMPLE",
        "SAMPLE",
        "SAMPLE",
        "SAMPLE",
        "SAMPLE",
        "MODELED",
        "SAMPLE",
        "SAMPLE",
        "MODELED",
        "MEASURED",
        "REPORTED",
        "SAMPLE",
        "MODELED",
        "UNAVAILABLE"
      ],
      "width": 768
    },
    {
      "activeScene": "executive",
      "bodyClientWidth": 1009,
      "bodyScrollWidth": 1009,
      "documentClientWidth": 1009,
      "documentScrollWidth": 1009,
      "externalResources": [],
      "forcedColorsQuery": false,
      "height": 768,
      "horizontalOverflowPx": 0,
      "innerHeight": 768,
      "innerWidth": 1024,
      "passed": true,
      "reducedMotionQuery": false,
      "rightmostRenderedPixel": 1009,
      "screenshot": "C:/Users/steph/Documents/Codex/2026-09-04/get-this-fuully-oeprational-and-real/work/lyte-services/artifacts/benchmarks/browser/screenshots/lyte-1024x768.png",
      "url": "http://127.0.0.1:7860/static/lyte/index.html#executive",
      "visibleTruthLabels": [
        "MEASURED",
        "SAMPLE",
        "SAMPLE",
        "MODELED",
        "MODELED",
        "SAMPLE",
        "SAMPLE",
        "SAMPLE",
        "SAMPLE",
        "SAMPLE",
        "MODELED",
        "SAMPLE",
        "SAMPLE",
        "MODELED",
        "MEASURED",
        "REPORTED",
        "SAMPLE",
        "MODELED",
        "UNAVAILABLE"
      ],
      "width": 1024
    },
    {
      "activeScene": "executive",
      "bodyClientWidth": 1425,
      "bodyScrollWidth": 1425,
      "documentClientWidth": 1425,
      "documentScrollWidth": 1425,
      "externalResources": [],
      "forcedColorsQuery": false,
      "height": 900,
      "horizontalOverflowPx": 0,
      "innerHeight": 900,
      "innerWidth": 1440,
      "passed": true,
      "reducedMotionQuery": false,
      "rightmostRenderedPixel": 1425,
      "screenshot": "C:/Users/steph/Documents/Codex/2026-09-04/get-this-fuully-oeprational-and-real/work/lyte-services/artifacts/benchmarks/browser/screenshots/lyte-1440x900.png",
      "url": "http://127.0.0.1:7860/static/lyte/index.html#executive",
      "visibleTruthLabels": [
        "MEASURED",
        "SAMPLE",
        "SAMPLE",
        "MODELED",
        "MODELED",
        "SAMPLE",
        "SAMPLE",
        "SAMPLE",
        "SAMPLE",
        "SAMPLE",
        "MODELED",
        "SAMPLE",
        "SAMPLE",
        "MODELED",
        "MEASURED",
        "REPORTED",
        "SAMPLE",
        "MODELED",
        "UNAVAILABLE"
      ],
      "width": 1440
    },
    {
      "activeScene": "executive",
      "bodyClientWidth": 1905,
      "bodyScrollWidth": 1905,
      "documentClientWidth": 1905,
      "documentScrollWidth": 1905,
      "externalResources": [],
      "forcedColorsQuery": false,
      "height": 1080,
      "horizontalOverflowPx": 0,
      "innerHeight": 1080,
      "innerWidth": 1920,
      "passed": true,
      "reducedMotionQuery": false,
      "rightmostRenderedPixel": 1905,
      "screenshot": "C:/Users/steph/Documents/Codex/2026-09-04/get-this-fuully-oeprational-and-real/work/lyte-services/artifacts/benchmarks/browser/screenshots/lyte-1920x1080.png",
      "url": "http://127.0.0.1:7860/static/lyte/index.html#executive",
      "visibleTruthLabels": [
        "MEASURED",
        "SAMPLE",
        "SAMPLE",
        "MODELED",
        "MODELED",
        "SAMPLE",
        "SAMPLE",
        "SAMPLE",
        "SAMPLE",
        "SAMPLE",
        "MODELED",
        "SAMPLE",
        "SAMPLE",
        "MODELED",
        "MEASURED",
        "REPORTED",
        "SAMPLE",
        "MODELED",
        "UNAVAILABLE"
      ],
      "width": 1920
    }
  ]
}
```

Limitations:

- This run covers one Chromium implementation; Safari and Firefox are not inferred.
- Forced-colors and reduced-motion support remain separately checked by static contract tests unless those OS preferences are active for this browser run.
- Screenshots contain the public SAMPLE / MODELED scenario and no customer telemetry.

### Bundle size

Result: `MEASURED_PARITY`. Measurement: 29353 gzip_bytes.

Evidence: `lyte/ui/index.html`; `lyte/ui/styles.css`; `lyte/ui/app.js`.

Raw measurements:

```json
{
  "compression": "gzip level 9 with mtime=0",
  "files": [
    {
      "gzip_bytes": 8725,
      "path": "lyte/ui/index.html",
      "raw_bytes": 36882,
      "sha256": "b96651f2869438f4c0fa51642f322f52cb60768bab0474b9922c4a0007128830"
    },
    {
      "gzip_bytes": 9707,
      "path": "lyte/ui/styles.css",
      "raw_bytes": 50982,
      "sha256": "5642f6bd265f528602e9e5c661dda6844f87923c652f1f716964eb2843717176"
    },
    {
      "gzip_bytes": 10921,
      "path": "lyte/ui/app.js",
      "raw_bytes": 43018,
      "sha256": "e2ec0964bac69f9ea582c50620bf0071e41c83b173609a9a63cf4fbe9465eb32"
    }
  ],
  "gzip_bytes": 29353,
  "raw_bytes": 130882,
  "scope": "first-party frontend HTML, CSS, and JavaScript; dependencies and container excluded"
}
```

Limitations:

- Dependency closure and container image layers are outside this frontend bundle scope.

### Resource consumption

Result: `MEASURED_PARITY`. Measurement: peak RSS 91734016 bytes; CPU 1.28125 s; wall 2.7540482 s.

Evidence: `operating-system process counters`; `Python process_time and perf_counter`.

Raw measurements:

```json
{
  "cpu_time_seconds": 1.28125,
  "measurement_boundary": "one runner process including fixture startup repetitions, query workload, OTLP ingest, SQLite persistence, and artifact preparation",
  "peak_rss_before_bytes": 80134144,
  "peak_rss_before_method": "Windows PeakWorkingSetSize",
  "peak_rss_bytes": 91734016,
  "peak_rss_method": "Windows PeakWorkingSetSize",
  "wall_time_seconds": 2.7540482
}
```

Limitations:

- Peak RSS is process-lifetime high-water memory, not an isolated per-request sample.
- CPU and wall time include benchmark orchestration, application startup, queries, and ingest; host background load is not controlled.
- Container and whole-host resources are not inferred from this process measurement.

## Overall limitations

- This is a local in-process benchmark over an explicitly SAMPLE fixture; it is not a production load test.
- SQLite results do not establish PostgreSQL, distributed, container, or hosted-service performance.
- No external vendor runtime was tested, so this report makes no comparative superiority claim.
- Browser evidence covers the recorded Chromium build and declared viewports; other browser engines are not inferred.
- Host background load was not isolated; raw samples are retained so another environment can rerun the same protocol.
- When working_tree_state is modified or unknown, the source revision identifies the baseline only and does not bind uncommitted changes.

The JSON artifact is canonical. This Markdown report and the CSV capability matrix were generated from the same in-memory document.
