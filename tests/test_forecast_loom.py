from __future__ import annotations

import math

import pytest

from lyte.intelligence.forecast_loom import (
    ForecastError,
    ForecastRequest,
    RobustDriftProvider,
    run_forecast,
)


def test_forecast_is_deterministic_and_receipted() -> None:
    request = ForecastRequest(
        signal_id="cpu.utilization",
        values=(10.0, 11.0, None, 13.0, 14.0),
        horizon=3,
    )
    first = run_forecast(request)
    second = run_forecast(request)

    assert first == second
    assert first.receipt.imputed_points == 1
    assert first.receipt.context_points == 5
    assert first.receipt.provider == "szl.robust-drift/v1"
    assert len(first.receipt.input_sha256) == 64
    assert len(first.receipt.output_sha256) == 64
    assert 0.0 <= first.receipt.confidence <= 1.0


def test_long_context_is_bounded_to_8192_points() -> None:
    values = tuple(float(i) for i in range(9_000))
    forecast = run_forecast(
        ForecastRequest(signal_id="transactions", values=values, horizon=1)
    )
    assert forecast.receipt.context_points == 8_192


def test_quantiles_are_monotonic() -> None:
    forecast = run_forecast(
        ForecastRequest(
            signal_id="latency.p95",
            values=(100.0, 101.0, 99.0, 102.0, 103.0),
            horizon=4,
        )
    )
    for point in forecast.points:
        values = list(point.quantiles.values())
        assert values == sorted(values)


def test_rejects_all_missing_context() -> None:
    with pytest.raises(ForecastError, match="entirely missing"):
        run_forecast(
            ForecastRequest(signal_id="signal", values=(None, None), horizon=1)
        )


def test_rejects_non_finite_input() -> None:
    with pytest.raises(ForecastError, match="non-finite"):
        run_forecast(
            ForecastRequest(signal_id="signal", values=(1.0, math.inf), horizon=1)
        )


def test_rejects_quantiles_without_median() -> None:
    with pytest.raises(ForecastError, match="median"):
        run_forecast(
            ForecastRequest(
                signal_id="signal",
                values=(1.0, 2.0),
                horizon=1,
                quantiles=(0.1, 0.9),
            )
        )


class CrossingProvider(RobustDriftProvider):
    name = "test.crossing"

    def forecast(self, values, *, horizon, quantiles):  # type: ignore[no-untyped-def]
        del values
        return [{q: float(len(quantiles) - index) for index, q in enumerate(quantiles)}] * horizon


def test_rejects_crossing_provider_quantiles() -> None:
    with pytest.raises(ForecastError, match="crossing"):
        run_forecast(
            ForecastRequest(signal_id="signal", values=(1.0, 2.0), horizon=2),
            provider=CrossingProvider(),
        )
