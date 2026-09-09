"""Governed zero-shot-style forecasting primitives for Lyte.

Forecast Loom is an SZL-native adaptation layer for operational time-series
forecasting. It borrows broad frontier ideas (long context, probabilistic
quantiles, missing-value handling, zero-shot evaluation) without copying model
weights, architecture code, or branding from any upstream project.

The module intentionally keeps the governance boundary separate from any model
provider. A provider may be a local deterministic baseline, an admitted Hub
model, or a future sovereign SZL model. Every forecast is normalized into the
same evidence-bearing contract before it can enter Lyte.
"""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Protocol

MAX_CONTEXT = 8_192
DEFAULT_QUANTILES = (0.10, 0.25, 0.50, 0.75, 0.90)
CONTRACT_VERSION = "szl.lyte.forecast-loom/v1"


class ForecastError(ValueError):
    """Raised when a forecasting request violates the Lyte contract."""


@dataclass(frozen=True)
class ForecastRequest:
    signal_id: str
    values: tuple[float | None, ...]
    horizon: int
    quantiles: tuple[float, ...] = DEFAULT_QUANTILES


@dataclass(frozen=True)
class ForecastPoint:
    step: int
    quantiles: dict[str, float]


@dataclass(frozen=True)
class ForecastReceipt:
    contract: str
    signal_id: str
    provider: str
    context_points: int
    imputed_points: int
    horizon: int
    quantiles: tuple[float, ...]
    input_sha256: str
    output_sha256: str
    confidence: float
    limitations: tuple[str, ...]


@dataclass(frozen=True)
class GovernedForecast:
    points: tuple[ForecastPoint, ...]
    receipt: ForecastReceipt


class ForecastProvider(Protocol):
    """Provider boundary for admitted forecasting engines."""

    name: str

    def forecast(
        self,
        values: Sequence[float],
        *,
        horizon: int,
        quantiles: Sequence[float],
    ) -> Sequence[dict[float, float]]:
        """Return one quantile mapping for each forecast step."""


def _canonical(payload: object) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _sha256(payload: object) -> str:
    return hashlib.sha256(_canonical(payload)).hexdigest()


def _validate_quantiles(quantiles: Sequence[float]) -> tuple[float, ...]:
    normalized = tuple(float(q) for q in quantiles)
    if not normalized:
        raise ForecastError("at least one quantile is required")
    if tuple(sorted(set(normalized))) != normalized:
        raise ForecastError("quantiles must be strictly increasing and unique")
    if any(q <= 0.0 or q >= 1.0 or not math.isfinite(q) for q in normalized):
        raise ForecastError("quantiles must be finite values strictly between 0 and 1")
    if 0.5 not in normalized:
        raise ForecastError("quantiles must include the median (0.5)")
    return normalized


def _impute(values: Sequence[float | None]) -> tuple[tuple[float, ...], int]:
    if not values:
        raise ForecastError("forecast context cannot be empty")
    if len(values) > MAX_CONTEXT:
        values = values[-MAX_CONTEXT:]

    valid = [float(v) for v in values if v is not None]
    if not valid:
        raise ForecastError("forecast context cannot be entirely missing")
    if any(not math.isfinite(v) for v in valid):
        raise ForecastError("forecast context contains a non-finite value")

    fallback = statistics.median(valid)
    filled: list[float] = []
    last: float | None = None
    imputed = 0
    for raw in values:
        if raw is None:
            imputed += 1
            filled.append(last if last is not None else fallback)
            continue
        value = float(raw)
        filled.append(value)
        last = value
    return tuple(filled), imputed


class RobustDriftProvider:
    """Dependency-free baseline for honest pre-model admission testing.

    The baseline combines a robust median slope with median absolute deviation.
    It is deliberately simple and deterministic. It is not presented as a
    frontier model; its purpose is to provide a measurable fallback and a hard
    benchmark that an external or sovereign model must beat before admission.
    """

    name = "szl.robust-drift/v1"

    @staticmethod
    def _slope(values: Sequence[float]) -> float:
        if len(values) < 2:
            return 0.0
        window = values[-min(len(values), 64) :]
        slopes = [b - a for a, b in zip(window, window[1:], strict=False)]
        return statistics.median(slopes) if slopes else 0.0

    @staticmethod
    def _scale(values: Sequence[float]) -> float:
        center = statistics.median(values)
        deviations = [abs(v - center) for v in values]
        mad = statistics.median(deviations)
        return max(1e-9, 1.4826 * mad)

    def forecast(
        self,
        values: Sequence[float],
        *,
        horizon: int,
        quantiles: Sequence[float],
    ) -> Sequence[dict[float, float]]:
        slope = self._slope(values)
        scale = self._scale(values[-min(len(values), 256) :])
        anchor = values[-1]
        output: list[dict[float, float]] = []
        for step in range(1, horizon + 1):
            median = anchor + slope * step
            spread = scale * math.sqrt(step)
            row: dict[float, float] = {}
            for q in quantiles:
                z = math.log(q / (1.0 - q)) / 1.7
                row[float(q)] = median + z * spread
            output.append(row)
        return output


def _confidence(values: Sequence[float], imputed: int, horizon: int) -> float:
    density = 1.0 - (imputed / max(1, len(values)))
    horizon_penalty = 1.0 / (1.0 + horizon / max(8.0, len(values)))
    context_reward = min(1.0, math.log2(len(values) + 1) / 10.0)
    return round(max(0.0, min(1.0, density * horizon_penalty * context_reward)), 4)


def run_forecast(
    request: ForecastRequest,
    provider: ForecastProvider | None = None,
) -> GovernedForecast:
    """Execute a forecast and emit a deterministic proof receipt."""

    if not request.signal_id.strip():
        raise ForecastError("signal_id is required")
    if request.horizon < 1 or request.horizon > 1_024:
        raise ForecastError("horizon must be between 1 and 1024")

    quantiles = _validate_quantiles(request.quantiles)
    values, imputed = _impute(request.values)
    engine = provider or RobustDriftProvider()
    raw = tuple(engine.forecast(values, horizon=request.horizon, quantiles=quantiles))
    if len(raw) != request.horizon:
        raise ForecastError("provider returned an unexpected number of forecast steps")

    points: list[ForecastPoint] = []
    for step, row in enumerate(raw, start=1):
        observed: list[float] = []
        normalized: dict[str, float] = {}
        for q in quantiles:
            if q not in row:
                raise ForecastError(f"provider omitted quantile {q}")
            value = float(row[q])
            if not math.isfinite(value):
                raise ForecastError("provider returned a non-finite forecast")
            observed.append(value)
            normalized[f"q{int(round(q * 100)):02d}"] = value
        if observed != sorted(observed):
            raise ForecastError("provider returned crossing quantiles")
        points.append(ForecastPoint(step=step, quantiles=normalized))

    input_payload = {
        "contract": CONTRACT_VERSION,
        "signal_id": request.signal_id,
        "values": values,
        "horizon": request.horizon,
        "quantiles": quantiles,
    }
    output_payload = [asdict(point) for point in points]
    receipt = ForecastReceipt(
        contract=CONTRACT_VERSION,
        signal_id=request.signal_id,
        provider=engine.name,
        context_points=len(values),
        imputed_points=imputed,
        horizon=request.horizon,
        quantiles=quantiles,
        input_sha256=_sha256(input_payload),
        output_sha256=_sha256(output_payload),
        confidence=_confidence(values, imputed, request.horizon),
        limitations=(
            "Forecasts are advisory signals, not autonomous execution authority.",
            "Provider admission requires measured benchmark evidence against SZL baselines.",
            "Confidence is an operational evidence score, not calibrated probability.",
        ),
    )
    return GovernedForecast(points=tuple(points), receipt=receipt)
