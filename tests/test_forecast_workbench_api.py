"""Real-app workbench integration. Run with the existing repository dependencies."""
from __future__ import annotations

import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from lyte.api import routes_forecast_workbench, routes_health
from lyte.app import create_app

REVISION = "a" * 40  # Test fixture, not the deployed revision.
PREFIX = "/api/lyte/v2/forecast"


@pytest.fixture
def client(monkeypatch, tmp_path):
    for key in ("GITHUB_SHA", "SOURCE_REVISION", "LYTE_GRANITE_ENABLED"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("LYTE_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    monkeypatch.setenv("LYTE_DEMO_MODE", "true")
    monkeypatch.setenv("LYTE_SOURCE_REVISION", REVISION)
    monkeypatch.setattr(routes_health, "_ROOT", tmp_path)
    with TestClient(create_app()) as connection:
        yield connection


def payload():
    return {
        "signal_id": "sample.cpu", "values": [40, 42, None, 45, 48, 51, 53, 55],
        "horizon": 12, "quantiles": [0.1, 0.5, 0.9], "provider": "baseline",
        "risk": {"threshold": 80, "direction": "above", "alert_level": 0.8},
    }


def test_inspection_uses_unchanged_real_forecast_and_persists_nothing(client):
    before = client.get("/api/lyte/v2/receipts").json()["count"]
    request = payload()
    original = client.post(PREFIX, json=request)
    inspected = client.post(PREFIX + "/inspect", json=request)
    assert original.status_code == inspected.status_code == 200
    envelope = inspected.json()
    assert envelope["sha256"] == hashlib.sha256(envelope["canonical_json"].encode()).hexdigest()
    body = json.loads(envelope["canonical_json"])
    assert body["forecast"] == original.json()
    assert body["source"] == {"repository": "szl-holdings/lyte-services", "revision": REVISION}
    assert body["request"]["values"] == request["values"]
    assert body["persisted"] is False
    assert client.get("/api/lyte/v2/receipts").json()["count"] == before
    assert inspected.headers["cache-control"] == "no-store"


def test_workbench_and_assets_use_existing_app_and_security_headers(client):
    manifest = client.get(PREFIX).json()
    assert manifest["inspection_provider"] == "baseline"
    assert manifest["execution_authority"] == "NONE"
    page = client.get(manifest["workbench"])
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    assert "Forecast Loom | Lyte" in page.text
    assert 'id="forecast-form"' in page.text and 'type="module"' in page.text
    assert "default-src 'self'" in page.headers["content-security-policy"]
    for path in ("forecast.css", "forecast.mjs"):
        assert client.get("/static/lyte/" + path).status_code == 200
    assert "prefers-reduced-motion" in client.get("/static/lyte/forecast.css").text
    assert "forced-colors" in client.get("/static/lyte/forecast.css").text


@pytest.mark.parametrize("change", [
    {"risk": None}, {"provider": "granite"}, {"horizon": 0},
    {"risk": {"threshold": "NaN"}}, {"risk": {"threshold": 80, "execute": True}},
    {"values": [None, None]}, {"quantiles": [0.25, 0.5, 0.75]},
])
def test_invalid_or_unadmitted_inspection_request_fails(client, change):
    assert client.post(PREFIX + "/inspect", json={**payload(), **change}).status_code == 422


def test_unbound_or_conflicting_source_fails_before_calculation(client, monkeypatch):
    monkeypatch.delenv("LYTE_SOURCE_REVISION")
    assert client.post(PREFIX + "/inspect", json=payload()).status_code == 503
    monkeypatch.setenv("LYTE_SOURCE_REVISION", REVISION)
    monkeypatch.setenv("SOURCE_REVISION", "b" * 40)
    assert client.post(PREFIX + "/inspect", json=payload()).status_code == 503


def test_anonymous_mutations_and_granite_default_stay_denied(client):
    assert client.post("/api/lyte/v2/analyze", json={}).status_code in {401, 503}
    response = client.post(PREFIX, json={**payload(), "provider": "granite"})
    assert response.status_code == 503
    assert response.json()["detail"] == "Granite provider is not admitted in this deployment"


def test_source_change_during_computation_returns_no_envelope(client, monkeypatch):
    revisions = iter((REVISION, "b" * 40))
    monkeypatch.setattr(routes_forecast_workbench, "source_revision", lambda: next(revisions))
    result = client.post(PREFIX + "/inspect", json=payload())
    assert result.status_code == 503
    assert "canonical_json" not in result.json()
