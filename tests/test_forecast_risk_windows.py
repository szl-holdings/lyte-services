"""Exact quantile mathematics and API regressions; no checkpoint downloads."""

from dataclasses import asdict, replace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lyte.api.routes_forecast import router
from lyte.intelligence.forecast_loom import ForecastError, ForecastRequest, run_forecast
from lyte.intelligence.forecast_risk import ThresholdRule, _digest, derive_risk_window


class FixedProvider:
    name = "test.fixed-quantiles"

    def __init__(self, rows):
        self.rows = rows

    def forecast(self, values, *, horizon, quantiles):
        return self.rows


def governed(rows):
    quantiles = tuple(sorted(rows[0]))
    return run_forecast(
        ForecastRequest("test.signal", (1.0, 2.0, 3.0), len(rows), quantiles),
        FixedProvider(rows),
    )


@pytest.mark.parametrize("direction", ["above", "below"])
@pytest.mark.parametrize("threshold", [-1.0, 0.0, 0.5, 1.0, 1.5, 2.0, 3.0])
@pytest.mark.parametrize("weights", [(1, 1, 8), (5, 0, 5), (0, 10, 0), (2, 5, 3)])
def test_discrete_distribution_probability_is_inside_conditional_bounds(
    direction, threshold, weights
):
    # Exact generalized inverse quantiles of known finite distributions test
    # atoms, ties, sparse supports, and strict >/< event semantics.
    support = [float(i) for i, weight in enumerate(weights) for _ in range(weight)]
    quantiles = (0.1, 0.5, 0.9)
    row = {q: support[int(q * len(support)) - 1] for q in quantiles}
    report = derive_risk_window(governed([row]), ThresholdRule(threshold, direction))
    observed = report["steps"][0]
    event = (lambda x: x > threshold) if direction == "above" else (lambda x: x < threshold)
    truth = sum(event(x) for x in support) / len(support)
    assert observed["model_implied_breach_lower"] <= truth + 1e-12
    assert observed["model_implied_breach_upper"] >= truth - 1e-12


def test_alert_steps_are_marginal_not_first_passage():
    prediction = governed([
        {0.1: 0.0, 0.5: 2.0, 0.9: 4.0},
        {0.1: 3.0, 0.5: 6.0, 0.9: 9.0},
        {0.1: 7.0, 0.5: 8.0, 0.9: 10.0},
    ])
    report = derive_risk_window(prediction, ThresholdRule(5.0, alert_level=0.8))
    assert report["earliest_possible_alert_step"] == 2
    assert report["earliest_supported_alert_step"] == 3
    assert report["first_median_crossing_step"] == 2
    assert report["calibration_status"] == "NOT_ESTABLISHED"
    assert report["execution_authority"] == "NONE"
    assert report["production_admitted"] is False


def test_union_bounds_do_not_assume_independent_steps():
    prediction = governed([{0.1: 0.0, 0.5: 1.0, 0.9: 2.0}] * 3)
    report = derive_risk_window(prediction, ThresholdRule(1.5))
    bounds = report["any_breach_over_horizon"]
    assert bounds["lower"] == pytest.approx(0.1)
    assert bounds["upper"] == 1.0
    # Perfectly correlated events can have the same union probability as a
    # single step: multiplying independent survival probabilities is unjustified.
    assert bounds["lower"] <= 0.2 <= bounds["upper"]


def test_median_only_remains_wide_and_null_does_not_claim_safety():
    report = derive_risk_window(governed([{0.5: 1.0}]), ThresholdRule(5.0, alert_level=0.8))
    assert report["steps"][0]["model_implied_breach_lower"] == 0
    assert report["steps"][0]["model_implied_breach_upper"] == 0.5
    assert report["earliest_possible_alert_step"] is None
    assert report["earliest_supported_alert_step"] is None


def test_report_binds_rule_and_complete_forecast_receipt():
    prediction = governed([{0.1: 1.0, 0.5: 2.0, 0.9: 3.0}])
    first = derive_risk_window(prediction, ThresholdRule(4.0))
    assert first == derive_risk_window(prediction, ThresholdRule(4.0))
    other_rule = derive_risk_window(prediction, ThresholdRule(5.0))
    assert first["report_sha256"] != other_rule["report_sha256"]
    changed = replace(prediction, receipt=replace(prediction.receipt, provider="test.other"))
    other_provider = derive_risk_window(changed, ThresholdRule(4.0))
    assert first["report_sha256"] != other_provider["report_sha256"]
    report_hash = first.pop("report_sha256")
    assert report_hash == _digest(first)


def test_changed_forecast_output_is_rejected():
    prediction = governed([{0.1: 1.0, 0.5: 2.0, 0.9: 3.0}])
    prediction.points[0].quantiles["q50"] = 100.0
    with pytest.raises(ForecastError, match="digest mismatch"):
        derive_risk_window(prediction, ThresholdRule(4.0))


def test_rehashed_crossing_quantiles_are_still_rejected():
    prediction = governed([{0.1: 1.0, 0.5: 2.0, 0.9: 3.0}])
    prediction.points[0].quantiles["q50"] = 100.0
    receipt = replace(
        prediction.receipt,
        output_sha256=_digest([asdict(point) for point in prediction.points]),
    )
    with pytest.raises(ForecastError, match="crossing"):
        derive_risk_window(replace(prediction, receipt=receipt), ThresholdRule(4.0))


@pytest.mark.parametrize("rule", [
    ThresholdRule(float("nan")), ThresholdRule(float("inf")), ThresholdRule(True),
    ThresholdRule(1.0, "sideways"), ThresholdRule(1.0, alert_level=0),
    ThresholdRule(1.0, alert_level=1.1), ThresholdRule(1.0, alert_level=float("nan")),
])
def test_invalid_rule_fails_closed(rule):
    with pytest.raises(ForecastError):
        derive_risk_window(governed([{0.5: 1.0}]), rule)


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as connection:
        yield connection


def payload():
    return {"signal_id": "test.cpu", "values": [1, 2, 3], "horizon": 3}


def test_existing_api_response_shape_is_preserved(client):
    response = client.post("/api/v1/forecast", json=payload())
    assert response.status_code == 200
    assert set(response.json()) == {"points", "receipt", "execution_authority"}


def test_api_optional_risk_is_computed_from_same_forecast(client):
    request = {**payload(), "risk": {"threshold": 4, "direction": "above", "alert_level": 0.8}}
    response = client.post("/api/v1/forecast", json=request)
    assert response.status_code == 200
    data = response.json()
    risk = data["risk_window"]
    assert risk["forecast_output_sha256"] == data["receipt"]["output_sha256"]
    assert risk["execution_authority"] == "NONE"
    assert risk["calibration_status"] == "NOT_ESTABLISHED"


@pytest.mark.parametrize("risk", [
    {"threshold": "NaN"}, {"threshold": 4, "alert_level": 0},
    {"threshold": 4, "direction": "above_or_equal"}, {"threshold": 4, "execute": True},
])
def test_invalid_api_rule_returns_422(client, risk):
    assert client.post("/api/v1/forecast", json={**payload(), "risk": risk}).status_code == 422


def test_granite_enabled_without_revision_still_fails_closed(client, monkeypatch):
    monkeypatch.setenv("LYTE_GRANITE_ENABLED", "true")
    monkeypatch.delenv("LYTE_GRANITE_REVISION", raising=False)
    response = client.post("/api/v1/forecast", json={**payload(), "provider": "granite"})
    assert response.status_code == 503
    assert response.json()["detail"] == "Granite provider unavailable"


def test_granite_remains_disabled_by_default(client, monkeypatch):
    monkeypatch.delenv("LYTE_GRANITE_ENABLED", raising=False)
    response = client.post("/api/v1/forecast", json={**payload(), "provider": "granite"})
    assert response.status_code == 503
    assert "not admitted" in response.json()["detail"]


def test_decimal_quantile_boundary_does_not_suppress_alert():
    prediction = governed([{0.1: 0.0, 0.5: 1.0, 0.9: 2.0}])
    report = derive_risk_window(prediction, ThresholdRule(1.5, alert_level=0.1))
    assert report["steps"][0]["model_implied_breach_lower"] == 0.1
    assert report["earliest_supported_alert_step"] == 1
