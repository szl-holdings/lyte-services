# Forecast Loom: from predictions to bounded threshold-risk windows

Status: candidate source implementation. Local isolated tests are not full-repository CI,
real-checkpoint evaluation, production admission, public deployment proof, or calibration.

## The SZL adaptation

IBM supplies an attributed forecasting model. SZL owns the decision contract: turn a
forecast into a threshold-risk window, bind that window to its inputs and policy, and
preserve uncertainty through approval and outcome review. This extends Forecast Loom;
it does not create another product, Space, publisher, or model brand. The probability
inequalities are standard mathematics, not a claim of a newly invented theorem.

Upstream: https://huggingface.co/ibm-granite/granite-timeseries-patchtst-fm-r2
Release source: https://huggingface.co/blog/ibm-research/ibm-releases-sota-granite-time-series
The upstream card states users may select Apache-2.0 or OpenMDW-1.0. Retain upstream
attribution and the applicable license notices; do not represent IBM weights as SZL-trained.

## Existing API, additive request

The mounted Lyte analysis route remains `POST /api/lyte/v2/forecast`. Its existing delegated
forecast handler now accepts an optional `risk` object. Without it, the response shape
is unchanged. The underlying router's local path is `/api/v1/forecast`; the new isolated
API tests exercise that handler, not a deployed application or ingress.

```json
{
  "signal_id": "sample.cpu.utilization",
  "values": [40, 42, 41, 45, 48, 51, 53, 55],
  "horizon": 6,
  "quantiles": [0.1, 0.5, 0.9],
  "provider": "baseline",
  "risk": {"threshold": 80, "direction": "above", "alert_level": 0.8}
}
```

This is synthetic sample input, not observed customer telemetry. The added `risk_window`
contains per-step model-implied breach brackets, the first step at which the upper bracket
reaches the alert level, the first step at which the lower bracket reaches that level,
and the first strict median crossing. These are marginal alert windows, not the probability
distribution of the first actual breach. Threshold comparisons are strictly `>` or `<`.
A null step means not established within the horizon, not safe.

The report includes `forecast_receipt_sha256`, `forecast_output_sha256`, and its own
`report_sha256`. The full forecast receipt binds raw missingness and provider identity;
the risk-report digest additionally binds the threshold, direction, and alert level.
Hashes detect content changes relative to a trusted digest. They do not authenticate a
signer, establish data provenance, calibrate a model, or prove a prediction correct.

## Honest probability treatment

For an upper-threshold event X > T, quantiles at or below T bound the exceedance probability
from above; quantiles strictly above T bound it from below. For X < T, use the corresponding
strict/non-strict comparisons in reverse. This handles tied quantiles and probability atoms.
No interpolation between sparse quantiles is used. Decimal complements preserve alert-level
boundary comparisons such as 1 - 0.9 = 0.1.

If each step's breach probability lies in [lower_t, upper_t], the probability of at least
one breach is bounded by max(lower_t) and min(1, sum(upper_t)), conditional on those marginal
bounds being valid. The implementation does not assume temporal independence. Wide bounds
are preserved rather than manufactured into a precise incident probability. Every report
states `calibration_status: NOT_ESTABLISHED`, `production_admitted: false`, and
`execution_authority: NONE`. These flags describe this advisory report; they never change
runtime settings or certify a provider.

## Reproducible Granite adapter

Granite remains disabled by default. When a separate authorized evaluation/deployment lane
selects it, provide an immutable 40-character Hub commit SHA via the constructor's `revision`
or `LYTE_GRANITE_REVISION`. No default revision is invented; missing pins, branches such as
`main`, short SHAs, and tags fail closed. Selecting a pin is not admission. An enabled runtime
without a valid revision returns the existing safe 503 response.

The exact revision is passed to `from_pretrained`, and provider identity contains model ID,
revision, configured context, and frequency. Adapter configuration is frozen so a cached
checkpoint cannot silently outlive a changed public identity. Input timestamps now use the
configured positive fixed sampling interval rather than always advancing by one hour.
Calendar-dependent, zero, and negative intervals are rejected. The timestamps are synthetic
indexing for an already regularly sampled series, not a claim of original event times.
Quantile extraction uses unique exact target-column names; suffix matching and rounded
aliases cannot silently select unrelated or colliding columns.

Python dependencies for the IBM lane remain optional. Record resolved dependency versions,
checkpoint SHA, data revision, hardware, and runtime source SHA alongside any actual model
benchmark. The adapter unit tests use a deliberately synthetic revision and model doubles.
They prove contracts, not that a real checkpoint loads or meets deployment SLOs.

## Admission and projection order

GitHub `szl-holdings/lyte-services` remains the source authority. Obtain exact-head full CI
and review before normal protected merge. Do not waive checks or existing authentication.
After merge, advance the existing source-owned Lyte pin in `szl-holdings/a11oy` and publish
through its canonical HF writer to `SZLHOLDINGS/lyte`. Do not introduce another HF writer.
Verify the mounted route and runtime identity against that exact merged source, then project
only observed status to `a-11-oy.com` and the receipts/known bounds to `a11oy.net`.
This change does not update domain code, deploy a Space, enable Granite, or alter SAMPLE labels.

## Next measured frontier, without another wrapper

Use the existing walk-forward evaluator to compare pinned Granite to robust drift,
last-value, and seasonal-naive baselines on authorized, representative operational telemetry.
Use training-only scaling, nonoverlapping holdouts, pinball loss, interval coverage, and
latency/memory measurements. Retain the historical synthetic results without relabeling them
as production evidence. External challengers such as TimesFM need their own verified artifact,
license and runtime review; this change makes no unmeasured leaderboard claim.

Evaluate threshold-alert precision, recall, lead-time distributions, missed-event costs,
and abstention rates on separately held-out real events. Only then consider a calibration
layer. Logged interventions must retain assignment, confounders, and outcome timing; an
observed improvement after an action is not by itself causal evidence of its benefit.

For the existing Lyte UI, the intended compact view is a forecast ribbon, a threshold line,
an alert-window bracket, and one evidence drawer. Preserve uncalibrated/SAMPLE labels,
keyboard access, reduced motion, 320px reflow, and readable probability brackets. This document
is a UI integration contract, not a claim that the frontend has been implemented or tested.
Keep existing second-brain/formula registries authoritative; this change does not invent,
rename, or replace the user's formulas or write learned policy from forecast output.

## Test command and limits

```sh
python -m pytest tests/test_forecast_risk_windows.py tests/test_granite_pinned_adapter.py -q
```

Local isolated result: 108 passed, including 56 discrete-distribution boundary cases.
The unchanged Forecast Loom source was verified against Git blob
`9cb958cfa33ef7529fe77b5b7b66e706db7736bf` before these tests. Local Python was 3.13.5,
pytest 9.0.2, FastAPI 0.128.2, Pydantic 2.13.4, pandas 2.2.3 and httpx 0.28.1.
These are not the exact versions pinned by repository CI. Seven pandas-specific adapter
cases explicitly skip when that optional dependency is absent. Full application tests,
repository lint, real IBM inference, live-route verification and deployment remain separate gates.
