from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lyte.api.routes_forecast import router
from lyte.intelligence.forecast_evaluation import EvaluationSeries, evaluate_walk_forward
from lyte.intelligence.forecast_loom import (
    ForecastError,
    ForecastRequest,
    RobustDriftProvider,
    quantile_key,
    run_forecast,
)


def test_fractional_quantiles_cannot_overwrite_each_other() -> None:
    quantiles = (0.101, 0.104, 0.5, 0.895, 0.899)
    result = run_forecast(ForecastRequest("cpu", (10.0, 11.0, 12.0), 2, quantiles))
    assert set(result.points[0].quantiles) == {"q10.1", "q10.4", "q50", "q89.5", "q89.9"}
    assert len(result.points[0].quantiles) == len(quantiles)


@pytest.mark.parametrize("q,key", [(0.1, "q10"), (0.5, "q50"), (0.9, "q90"), (0.01, "q01")])
def test_legacy_whole_percent_keys_are_unchanged(q: float, key: str) -> None:
    assert quantile_key(q) == key


def test_float_neighbors_keep_distinct_quantile_keys() -> None:
    assert quantile_key(0.1) != quantile_key(math.nextafter(0.1, 1.0))


def test_missing_and_observed_inputs_have_distinct_raw_receipts() -> None:
    missing = run_forecast(ForecastRequest("cpu", (10.0, None, 11.0), 1))
    observed = run_forecast(ForecastRequest("cpu", (10.0, 10.0, 11.0), 1))
    assert missing.receipt.input_sha256 == observed.receipt.input_sha256
    assert missing.receipt.raw_input_sha256 != observed.receipt.raw_input_sha256
    assert missing.receipt.output_sha256 == observed.receipt.output_sha256


def test_truncation_is_visible_and_bound_to_original_input() -> None:
    a = run_forecast(ForecastRequest("cpu", (0.0,) + (1.0,) * 8192, 1))
    b = run_forecast(ForecastRequest("cpu", (2.0,) + (1.0,) * 8192, 1))
    assert a.receipt.original_context_points == 8193
    assert a.receipt.context_points == 8192
    assert a.receipt.truncated_points == 1
    assert a.receipt.input_sha256 == b.receipt.input_sha256
    assert a.receipt.raw_input_sha256 != b.receipt.raw_input_sha256


def test_output_digest_is_independently_recomputable() -> None:
    result = run_forecast(ForecastRequest("cpu", (1.0, 2.0, 3.0), 2))
    payload = [asdict(point) for point in result.points]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    assert result.receipt.output_sha256 == hashlib.sha256(encoded).hexdigest()


@pytest.mark.parametrize("bad", [0, 1025, True, 1.5])
def test_invalid_core_horizons_fail_closed(bad: int) -> None:
    with pytest.raises(ForecastError, match="horizon"):
        run_forecast(ForecastRequest("cpu", (1.0, 2.0), bad))


def test_quantile_work_is_bounded() -> None:
    with pytest.raises(ForecastError, match="99"):
        run_forecast(ForecastRequest("cpu", (1.0, 2.0), 1, tuple(i / 200 for i in range(1, 200))))


def test_api_preserves_provenance_and_fractional_quantiles() -> None:
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        response = client.post("/api/v1/forecast", json={
            "signal_id": "cpu", "values": [1, None, 3], "horizon": 2,
            "quantiles": [0.101, 0.104, 0.5, 0.9],
        })
    assert response.status_code == 200
    body = response.json()
    assert body["execution_authority"] == "NONE"
    assert len(body["points"][0]["quantiles"]) == 4
    assert len(body["receipt"]["raw_input_sha256"]) == 64
    assert body["receipt"]["original_context_points"] == 3


def test_granite_remains_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LYTE_GRANITE_ENABLED", "false")
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        response = client.post("/api/v1/forecast", json={
            "signal_id": "cpu", "values": [1, 2, 3], "horizon": 1, "provider": "granite",
        })
    assert response.status_code == 503
    assert response.json()["detail"] == "Granite provider is not admitted in this deployment"


class RecordingProvider(RobustDriftProvider):
    name = "test.recording"

    def __init__(self) -> None:
        self.contexts: list[tuple[float, ...]] = []

    def forecast(self, values, *, horizon, quantiles):
        self.contexts.append(tuple(values))
        return super().forecast(values, horizon=horizon, quantiles=quantiles)


def _series(scale: float = 1.0) -> EvaluationSeries:
    return EvaluationSeries("test", tuple(scale * (10 + i + math.sin(i)) for i in range(32)), 2)


def _evaluate(item: EvaluationSeries | None = None, provider=None, **kwargs):
    return evaluate_walk_forward(
        [item or _series()], provider or RobustDriftProvider(),
        horizon=4, folds=3, min_context=8, context_limit=16, **kwargs,
    )


def test_walk_forward_exposes_no_holdout_to_provider() -> None:
    provider = RecordingProvider()
    item = _series()
    report = _evaluate(item, provider)
    origins = [row["origin_index"] for row in report["series"][0]["folds"]]
    assert origins == [20, 24, 28]
    for origin, context in zip(origins, provider.contexts, strict=True):
        assert context == item.values[origin - 16:origin]


def test_scaled_metrics_do_not_depend_on_signal_units() -> None:
    first, converted = _evaluate(_series()), _evaluate(_series(1000))
    for key in first["macro_mean_mase"]:
        assert first["macro_mean_mase"][key] == pytest.approx(converted["macro_mean_mase"][key])


def test_perfect_constant_series_is_explicitly_unscorable_not_fake_zero_mase() -> None:
    report = _evaluate(EvaluationSeries("constant", (1.0,) * 32))
    assert report["fully_scorable"] is False
    assert report["macro_mean_mase"]["candidate"] is None
    assert report["qualification"] == "NOT_ESTABLISHED"


def test_missing_holdout_is_never_imputed() -> None:
    values = list(_series().values)
    values[-1] = None
    with pytest.raises(ForecastError, match="holdout"):
        _evaluate(EvaluationSeries("test", tuple(values)))


def test_missing_training_points_remain_explicit() -> None:
    values = list(_series().values)
    values[8] = None
    report = _evaluate(EvaluationSeries("test", tuple(values)))
    assert report["series"][0]["folds"][0]["candidate_receipt"]["imputed_points"] == 1


def test_operator_declared_data_never_grants_production_admission() -> None:
    report = _evaluate(data_kind="operator_supplied")
    assert report["production_admitted"] is False
    assert report["execution_authority"] == "NONE"
    assert report["data_kind_attestation"] == "CALLER_DECLARED_NOT_INDEPENDENTLY_VERIFIED"


def test_evaluation_is_reproducible_and_receipt_bound() -> None:
    first, second = _evaluate(), _evaluate()
    assert first == second
    digest = first.pop("report_sha256")
    data = json.dumps(first, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    assert digest == hashlib.sha256(data).hexdigest()


@pytest.mark.parametrize("key,value", [("folds", 1), ("horizon", 0), ("context_limit", 8193)])
def test_evaluation_bounds_are_enforced(key: str, value: int) -> None:
    args = {"horizon": 4, "folds": 3, "min_context": 8, "context_limit": 16}
    args[key] = value
    with pytest.raises(ForecastError):
        evaluate_walk_forward([_series()], RobustDriftProvider(), **args)


def test_duplicate_signal_identifiers_are_rejected() -> None:
    with pytest.raises(ForecastError, match="unique"):
        evaluate_walk_forward([_series(), _series()], RobustDriftProvider())


def test_insufficient_history_is_rejected() -> None:
    with pytest.raises(ForecastError, match="insufficient"):
        _evaluate(EvaluationSeries("short", (1.0, 2.0)))


def test_nonfinite_series_is_rejected() -> None:
    with pytest.raises(ForecastError, match="non-finite"):
        _evaluate(EvaluationSeries("invalid", (1.0,) * 31 + (math.inf,)))


def test_candidate_equal_to_drift_cannot_claim_superiority() -> None:
    report = _evaluate()
    assert report["qualification"] == "NOT_ESTABLISHED"
    means = report["series"][0]["mean_mase"]
    assert means["candidate"] == means["robust_drift"]


@pytest.mark.parametrize("exception", [RuntimeError("private model path"), OSError("load failed")])
def test_lazy_provider_failure_returns_safe_unavailable(monkeypatch, exception) -> None:
    from lyte.api import routes_forecast

    class BrokenProvider(RobustDriftProvider):
        def forecast(self, values, *, horizon, quantiles):
            raise exception

    monkeypatch.setattr(routes_forecast, "_provider", lambda name: BrokenProvider())
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        response = client.post("/api/v1/forecast", json={
            "signal_id": "cpu", "values": [1, 2, 3], "horizon": 1, "provider": "granite",
        })
    assert response.status_code == 503
    assert response.json()["detail"] == "Granite provider unavailable"
    assert "private model path" not in response.text
