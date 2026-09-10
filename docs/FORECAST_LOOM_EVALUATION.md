# Forecast Loom: integrity and walk-forward evaluation

The canonical runtime remains advisory. Nothing in this evaluator grants
production model admission or operational execution authority.

## Runtime contract additions

Whole-percent keys retain their established representation (`q10`, `q50`, `q90`).
Fractional-percent quantiles now have lossless keys: `0.101` becomes `q10.1`, and
`0.104` becomes `q10.4`. The previous rounded labels could silently overwrite
one another. Requests are bounded to 99 quantiles and 1,024 forecast steps.

`input_sha256` keeps its existing meaning: the canonical, imputed, bounded model
input. `raw_input_sha256` separately binds the normalized original observations,
including missing values and any truncated prefix. `original_context_points`
and `truncated_points` make context reduction explicit. These are deterministic
content digests, not signatures, caller attestations, or accuracy guarantees.

Granite remains disabled by default. Failures during lazy model loading now
return a bounded HTTP 503 response rather than an unhandled provider exception.

## Evaluation example

```python
from lyte.intelligence.forecast_evaluation import EvaluationSeries, evaluate_walk_forward
from lyte.intelligence.forecast_loom import RobustDriftProvider

# Replace this synthetic example with authorized, chronological, evenly spaced
# observations. Do not publish customer observations in a public evidence repo.
series = EvaluationSeries("qualification.example", tuple(float(i) for i in range(256)), 24)
report = evaluate_walk_forward(
    [series],
    RobustDriftProvider(),  # Or an explicitly selected optional challenger.
    horizon=24,
    folds=3,
    min_context=64,
    context_limit=128,
    data_kind="synthetic",
)
assert report["production_admitted"] is False
assert report["execution_authority"] == "NONE"
```

The evaluator calls the actual Forecast Loom provider boundary, using only each
training prefix. Holdouts are non-overlapping and never imputed. Candidate
providers must be frozen or fitted only on the supplied prefixes. This cannot
establish whether a pretrained model has seen a dataset before.

Three comparators are reported: SZL robust drift, last value, and seasonal naive.
Raw MAE is retained per signal, not averaged across dollars, counts, and
percentages. Mean absolute scaled error (MASE) uses only observed seasonal
training differences as its denominator. Zero or missing denominators remain
unscorable; no epsilon invents a successful result. Equal-weight macro metrics
require complete scorable evidence across all signals and folds.

Quantile diagnostics include scaled pinball loss, empirical 80% interval
coverage, and scaled interval width. Ordered quantiles alone do not establish
calibration. Qualification requires improvement on every signal against its
strongest comparator, and still cannot enable production. A caller's
`operator_supplied` label does not establish production representativeness.

## Historical qualification limits

The earlier single-holdout synthetic report remains historical evidence. Its
89.73% aggregate raw-MAE reduction combines heterogeneous signal units and is
not a production accuracy claim or a scale-invariant comparison. Use the new
walk-forward report for future cross-signal evaluation. Do not rewrite historic
measurements or claim this harness has validated Granite until an actual
source-pinned challenger run produces evidence.

Production admission still requires authorized representative Lyte telemetry,
independently reviewable source/model/environment identity, and deployment SLO
evidence on the intended hardware. Publish runtime updates only through A11oy's
canonical Hugging Face writer, then verify the product projection before adding
any deployment-success assertion to a11oy.net.
