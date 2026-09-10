"""SZL threshold-risk windows derived from governed forecast quantiles.

Quantiles give marginal information, not temporal dependence or causality.
This module therefore reports brackets, never interpolated probabilities or
an invented first-passage distribution. All probabilities are conditional on
the model quantiles being valid; empirical calibration is not established.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Literal

from lyte.intelligence.forecast_loom import (
    CONTRACT_VERSION,
    ForecastError,
    GovernedForecast,
    quantile_key,
)

RISK_CONTRACT = "szl.lyte.forecast-risk-window/v1"


@dataclass(frozen=True)
class ThresholdRule:
    """A strict event (> or <), with an operator-selected alert level."""

    threshold: float
    direction: Literal["above", "below"] = "above"
    alert_level: float = 0.5


def _digest(payload: object) -> str:
    data = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _validate_rule(rule: ThresholdRule) -> None:
    for name, value in (("threshold", rule.threshold), ("alert_level", rule.alert_level)):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ForecastError(f"{name} must be a finite number")
        if not math.isfinite(value):
            raise ForecastError(f"{name} must be a finite number")
    if rule.direction not in {"above", "below"}:
        raise ForecastError("risk direction must be above or below")
    if not 0 < rule.alert_level <= 1:
        raise ForecastError("alert_level must be in (0, 1]")


def derive_risk_window(forecast: GovernedForecast, rule: ThresholdRule) -> dict[str, object]:
    """Bind a threshold decision envelope to the existing Forecast Loom receipt.

    For X > T, Q(q) <= T implies P(X > T) <= 1-q; Q(q) > T
    implies P(X > T) >= 1-q. Reverse the strict comparisons for X < T.
    This remains conservative for atoms/ties. For any breach over the horizon,
    max(lower_t) <= P(union events) <= min(1, sum(upper_t)); no independence
    assumption is introduced. These are conditional model bounds, not measured
    incident probabilities, confidence intervals, or execution permission.
    """
    _validate_rule(rule)
    receipt = forecast.receipt
    if receipt.contract != CONTRACT_VERSION:
        raise ForecastError("unsupported forecast contract for risk derivation")
    quantiles = receipt.quantiles
    if (
        not quantiles
        or len(quantiles) > 99
        or any(not math.isfinite(q) or not 0 < q < 1 for q in quantiles)
        or tuple(sorted(set(quantiles))) != quantiles
        or 0.5 not in quantiles
    ):
        raise ForecastError("risk derivation requires ordered finite forecast quantiles")
    if not 1 <= receipt.horizon <= 1024 or len(forecast.points) != receipt.horizon:
        raise ForecastError("risk derivation requires a complete bounded forecast horizon")
    if _digest([asdict(point) for point in forecast.points]) != receipt.output_sha256:
        raise ForecastError("forecast output digest mismatch")

    rows: list[dict[str, object]] = []
    expected_keys = {quantile_key(q) for q in quantiles}
    for step, point in enumerate(forecast.points, 1):
        if point.step != step or set(point.quantiles) != expected_keys:
            raise ForecastError("forecast step or quantile keys do not match the receipt")
        values = [point.quantiles[quantile_key(q)] for q in quantiles]
        if any(not math.isfinite(value) for value in values) or values != sorted(values):
            raise ForecastError("risk derivation rejects non-finite or crossing quantiles")
        pairs = list(zip(quantiles, values, strict=True))
        if rule.direction == "above":
            cdf_lower = max((q for q, value in pairs if value <= rule.threshold), default=0.0)
            cdf_upper = min((q for q, value in pairs if value > rule.threshold), default=1.0)
            # Preserve decimal quantile boundaries (1 - 0.9 must compare as
            # 0.1, not as 0.09999999999999998, at an operator alert level).
            lower = float(Decimal(1) - Decimal(str(cdf_upper)))
            upper = float(Decimal(1) - Decimal(str(cdf_lower)))
            median_crosses = point.quantiles[quantile_key(0.5)] > rule.threshold
        else:
            lower = max((q for q, value in pairs if value < rule.threshold), default=0.0)
            upper = min((q for q, value in pairs if value >= rule.threshold), default=1.0)
            median_crosses = point.quantiles[quantile_key(0.5)] < rule.threshold
        rows.append({
            "step": step,
            "model_implied_breach_lower": lower,
            "model_implied_breach_upper": upper,
            "median_crosses": median_crosses,
        })

    def first_at_or_above(key: str) -> int | None:
        return next((row["step"] for row in rows if row[key] >= rule.alert_level), None)

    report: dict[str, object] = {
        "contract": RISK_CONTRACT,
        "signal_id": receipt.signal_id,
        "provider": receipt.provider,
        "rule": asdict(rule),
        "event_semantics": (
            "STRICT_GREATER_THAN" if rule.direction == "above" else "STRICT_LESS_THAN"
        ),
        "time_unit": "FORECAST_STEPS_NOT_WALL_CLOCK",
        "steps": rows,
        "earliest_possible_alert_step": first_at_or_above("model_implied_breach_upper"),
        "earliest_supported_alert_step": first_at_or_above("model_implied_breach_lower"),
        "first_median_crossing_step": next(
            (row["step"] for row in rows if row["median_crosses"]), None
        ),
        "any_breach_over_horizon": {
            "lower": max(row["model_implied_breach_lower"] for row in rows),
            "upper": min(1.0, math.fsum(row["model_implied_breach_upper"] for row in rows)),
            "method": "MARGINAL_MAX_LOWER_UNION_SUM_UPPER_NO_INDEPENDENCE_ASSUMPTION",
        },
        "forecast_receipt_sha256": _digest(asdict(receipt)),
        "forecast_output_sha256": receipt.output_sha256,
        "calibration_status": "NOT_ESTABLISHED",
        "production_admitted": False,
        "execution_authority": "NONE",
        "limitations": [
            "Bounds are conditional on model quantiles; they are not calibrated incident risk.",
            "Marginal quantiles do not identify a joint trajectory or first-passage distribution.",
            "A null alert step means not established within this horizon, "
            "not that the signal is safe.",
            "Alert levels are operator policy; this report does not estimate intervention benefit.",
            "Digests bind content, not signer identity, real-world provenance, or accuracy.",
        ],
    }
    report["report_sha256"] = _digest(report)
    return report
