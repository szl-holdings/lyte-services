"""Leakage-bounded, scale-aware evaluation of Forecast Loom providers.

Only training prefixes cross the provider boundary. Holdouts are never imputed.
Raw MAE remains per signal: CPU percentages and dollars must not be averaged.
MASE uses an observed, training-only seasonal-naive denominator. A zero or
missing denominator is reported as unscorable, never replaced by an epsilon.
This module evaluates evidence; it never admits a production provider.
"""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Literal

from lyte.intelligence.forecast_loom import (
    MAX_CONTEXT,
    ForecastError,
    ForecastProvider,
    ForecastRequest,
    quantile_key,
    run_forecast,
)


@dataclass(frozen=True)
class EvaluationSeries:
    """An evenly spaced, oldest-first series; identity is caller-declared."""

    signal_id: str
    values: tuple[float | None, ...]
    season_length: int = 1


def _mean(values: Sequence[float]) -> float:
    result = statistics.fmean(values)
    if not math.isfinite(result):
        raise ForecastError("evaluation arithmetic produced a non-finite metric")
    return result


def _digest(value: object) -> str:
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _training_scale(values: Sequence[float | None], season: int) -> float | None:
    differences = [
        abs(float(values[i]) - float(values[i - season]))
        for i in range(season, len(values))
        if values[i] is not None and values[i - season] is not None
    ]
    if not differences:
        return None
    scale = _mean(differences)
    return scale if scale > 0 else None


def _scaled(value: float, scale: float | None) -> float | None:
    if scale is None:
        return None
    result = value / scale
    if not math.isfinite(result):
        raise ForecastError("evaluation arithmetic produced a non-finite metric")
    return result


def evaluate_walk_forward(
    series: Sequence[EvaluationSeries],
    provider: ForecastProvider,
    *,
    horizon: int = 48,
    folds: int = 3,
    min_context: int = 64,
    context_limit: int = 512,
    data_kind: Literal["synthetic", "operator_supplied"] = "synthetic",
) -> dict[str, object]:
    """Compare one provider to drift, last-value, and seasonal-naive baselines.

    Final non-overlapping holdouts define the folds. Later folds may train on
    observations that have become available by their origin, but not their own
    holdout or future observations. There is no train/test random shuffling.
    Candidate providers must themselves be frozen or fitted on prefixes only;
    pretraining contamination cannot be established by this evaluator.
    """
    for name, value, lower, upper in (
        ("horizon", horizon, 1, 1024),
        ("folds", folds, 2, 32),
        ("min_context", min_context, 2, MAX_CONTEXT),
        ("context_limit", context_limit, 2, MAX_CONTEXT),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or not lower <= value <= upper:
            raise ForecastError(f"{name} must be an integer in [{lower}, {upper}]")
    if min_context > context_limit:
        raise ForecastError("min_context cannot exceed context_limit")
    if not series or len(series) > 256:
        raise ForecastError("between 1 and 256 evaluation series are required")
    if data_kind not in {"synthetic", "operator_supplied"}:
        raise ForecastError("data_kind must be synthetic or operator_supplied")
    identifiers = [item.signal_id for item in series]
    if any(not name.strip() for name in identifiers) or len(set(identifiers)) != len(identifiers):
        raise ForecastError("evaluation signal identifiers must be nonempty and unique")

    records: list[dict[str, object]] = []
    quantiles = (0.1, 0.5, 0.9)
    for item in series:
        season = item.season_length
        if isinstance(season, bool) or not isinstance(season, int) or not 1 <= season < min_context:
            raise ForecastError("season_length must be positive and below min_context")
        if len(item.values) > MAX_CONTEXT + folds * horizon:
            raise ForecastError("evaluation series exceeds bounded context plus holdouts")
        if len(item.values) < min_context + folds * horizon:
            raise ForecastError("insufficient history for non-overlapping walk-forward folds")
        if any(v is not None and not math.isfinite(float(v)) for v in item.values):
            raise ForecastError("evaluation series contains non-finite observations")

        fold_records: list[dict[str, object]] = []
        for fold in range(folds):
            origin = len(item.values) - (folds - fold) * horizon
            train = item.values[max(0, origin - context_limit):origin]
            truth = item.values[origin:origin + horizon]
            if any(v is None for v in truth):
                raise ForecastError("missing holdout observations cannot be imputed for scoring")
            observed_truth = tuple(float(v) for v in truth if v is not None)
            request = ForecastRequest(item.signal_id, tuple(train), horizon, quantiles)
            candidate = run_forecast(request, provider)
            drift = run_forecast(request)
            observed_train = [float(v) for v in train if v is not None]
            last = [observed_train[-1]] * horizon
            # A seasonal baseline cannot manufacture missing lag observations.
            season_tail = train[-season:]
            seasonal = (
                [float(season_tail[i % season]) for i in range(horizon)]
                if all(v is not None for v in season_tail)
                else None
            )
            candidate_median = [p.quantiles[quantile_key(0.5)] for p in candidate.points]
            predictions = {
                "candidate": candidate_median,
                "robust_drift": [p.quantiles[quantile_key(0.5)] for p in drift.points],
                "last_value": last,
                "seasonal_naive": seasonal,
            }
            scale = _training_scale(train, season)
            mae = {
                key: _mean([abs(y - p) for y, p in zip(observed_truth, pred, strict=True)])
                if pred is not None else None
                for key, pred in predictions.items()
            }
            pinball = []
            covered = 0
            widths = []
            for y, point in zip(observed_truth, candidate.points, strict=True):
                lo = point.quantiles[quantile_key(0.1)]
                hi = point.quantiles[quantile_key(0.9)]
                covered += int(lo <= y <= hi)
                widths.append(hi - lo)
                for q in quantiles:
                    error = y - point.quantiles[quantile_key(q)]
                    pinball.append(max(q * error, (q - 1) * error))
            fold_records.append({
                "origin_index": origin,
                "training_points": len(train),
                "holdout_points": horizon,
                "training_scale": scale,
                "mae_per_signal_units": mae,
                "mase": {k: _scaled(v, scale) if v is not None else None for k, v in mae.items()},
                "scaled_pinball_loss": _scaled(_mean(pinball), scale),
                "nominal_interval_coverage": 0.8,
                "empirical_interval_coverage": covered / horizon,
                "scaled_interval_width": _scaled(_mean(widths), scale),
                "candidate_receipt": asdict(candidate.receipt),
            })
        # Complete-case aggregation avoids silently changing denominators.
        means: dict[str, float | None] = {}
        for name in ("candidate", "robust_drift", "last_value", "seasonal_naive"):
            values = [row["mase"][name] for row in fold_records]
            means[name] = _mean(values) if all(v is not None for v in values) else None
        baseline_means = [means[k] for k in ("robust_drift", "last_value", "seasonal_naive")]
        scorable = all(v is not None for v in [means["candidate"], *baseline_means])
        improves = bool(scorable and means["candidate"] < min(baseline_means))
        records.append({
            "signal_id": item.signal_id,
            "season_length": season,
            "folds": fold_records,
            "mean_mase": means,
            "fully_scorable": scorable,
            "beats_strongest_baseline": improves,
        })

    complete = all(row["fully_scorable"] for row in records)
    macro = {
        name: _mean([row["mean_mase"][name] for row in records]) if complete else None
        for name in ("candidate", "robust_drift", "last_value", "seasonal_naive")
    }
    report: dict[str, object] = {
        "schema": "szl.lyte.forecast-walk-forward/v1",
        "provider": provider.name,
        "data_kind": data_kind,
        "data_kind_attestation": "CALLER_DECLARED_NOT_INDEPENDENTLY_VERIFIED",
        "dataset_sha256": _digest([asdict(item) for item in series]),
        "horizon": horizon,
        "fold_count": folds,
        "context_limit": context_limit,
        "series": records,
        "macro_mean_mase": macro,
        "fully_scorable": complete,
        "qualification": (
            "IMPROVES_ALL_SERIES_OVER_STRONGEST_BASELINE"
            if complete and all(row["beats_strongest_baseline"] for row in records)
            else "NOT_ESTABLISHED"
        ),
        "production_admitted": False,
        "execution_authority": "NONE",
        "limitations": [
            "Caller-supplied series must be evenly spaced and in chronological order.",
            "This evaluator cannot establish pretraining contamination or "
            "production representativeness.",
            "Empirical interval coverage is diagnostic, not a calibration guarantee.",
            "Production admission requires separately verified telemetry and "
            "deployment SLO evidence.",
            "Content hashes are not signatures, identity attestations, or accuracy guarantees.",
        ],
    }
    report["report_sha256"] = _digest(report)
    return report
