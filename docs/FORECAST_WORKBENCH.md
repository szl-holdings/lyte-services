# Forecast Loom workbench — Python results, interactive evidence

This completes a user interface for the existing baseline/risk computation, not
another model, Space, service, or publisher. IBM remains an attributed optional
challenger. No Qwen, Granite, TRL, kernel, or production model is enabled here.

## Routes and packaging

GET `/api/lyte/v2/forecast` advertises the workbench capabilities.
GET `/api/lyte/v2/forecast/workbench` serves the interactive page from the existing
Lyte FastAPI application. The normal StaticFiles mount serves its CSS and JS.
POST `/api/lyte/v2/forecast/inspect` delegates once to the unchanged forecast
handler and returns a source-bound, byte-preserving evidence envelope.
The existing POST `/api/lyte/v2/forecast` remains backward compatible.
The root dashboard is unchanged; this workbench has its own explicit route.

## SZL adaptation

A compact ribbon shows q10/q90 and median, an independent threshold line and a
keyboard step inspector. Exact numbers and risk brackets have a tabular alternative.
An evidence drawer exports the actual received envelope. No synthetic forecast is
invented in JavaScript. The sample input is labeled SAMPLE; edits are caller supplied,
not independently observed telemetry. No wall-clock lead time is inferred from steps.

The browser verifies SHA-256 over the exact Python-generated UTF-8 JSON string.
It does not reserialize Python floats before hashing, avoiding 1.0/1 and -0.0/0
cross-runtime mismatches. It independently checks request/policy binding, quantile
ordering, strict threshold bounds and alert windows. The envelope is content integrity,
NOT a signature, authenticated real-world source, or proof of accuracy. Existing
forecast/report digests are preserved as evidence pointers, not relabeled signatures.

Missing/conflicting source identity returns503 before inference. Only the baseline
and an explicit risk rule are accepted. The existing authentication and authority
boundaries are unchanged; no persisted receipt is minted by the inspection route.
A source change during computation fails closed. Input edits abort an in-flight
request, hide old output, invalidate downloads and require a fresh explicit submit.
Requests use the same origin, omit credentials, have a20-second UI timeout, and
bound response size. Untrusted text is never inserted as HTML. No remote font,
script, canvas/WebGL dependency or worker is introduced.

## Validation commands

```sh
python -m pytest tests/test_forecast_inspection.py tests/test_forecast_workbench_api.py -q
node --test tests/forecast-workbench.test.mjs
python -m tools.forecast_workbench_browser --source-revision "$EXACT_GIT_SHA" --output /tmp/lyte-browser
```

The browser harness requires an isolated Playwright installation and Chromium.
It launches the actual application on loopback, not the public Space, and tests
320/375/768/1440px layouts, keyboard step inspection, exact evidence download,
input invalidation, reduced motion, forced colors and corrupted-response rejection.
The existing Python CI discovers both new Python suites; a read-only workflow
adds Node boundary tests without touching publication workflows or permissions.

At implementation time,18 pure Python tests and31 Node tests passed locally.
Full repository lint/tests and actual-browser results must be separately read back
from the candidate CI or isolated integration job. These local results do not claim
public deployment, calibrated forecasts, production telemetry, or Granite inference.

## Frontier release integration boundaries

Reuse existing Qwen wave Frontier#26 → Forge#170 → Nemo#8 → Serve#8 rather than
create another candidate. Qwen3.8-Flash-Next has Qwen-community-1.0 licensing and
hybrid attention. The documented TRL FSDP2 context-parallel recipe requires full
causal SDPA and cannot be assumed compatible with sliding/chunked attention.
Kernel native-repository migration is already tracked by Frontier#53 / Forge#203.
TRL1.13/core-stack qualification is already tracked by Frontier#61. Those sources,
licenses, exact revisions, hardware requirements and production HOLDs stay intact.

Primary references:
- https://huggingface.co/Qwen/Qwen3.8-Flash-Next
- https://huggingface.co/docs/trl/long_context_training
- https://huggingface.co/docs/kernels/migration
- https://huggingface.co/ibm-granite/granite-timeseries-patchtst-fm-r2

## Publication boundary

GitHub remains authoritative. After protected source admission, publication must
use the existing canonical A11oy writer and exact-source attestation; product and
proof surfaces follow actual runtime verification. This change does not edit the
previously blocked A11oy publisher files, bypass that block, change a source pin,
write directly to Hugging Face, or claim a public rollout. Do not promote these
application tests into model qualification or relabel SAMPLE as production data.
