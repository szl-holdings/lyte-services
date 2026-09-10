"""The hosted application path exposes real metrics, never a synthetic status."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from prometheus_client.parser import text_string_to_metric_families

from lyte import __version__
from lyte.api import routes_health
from lyte.app import create_app

PUBLIC_METRICS = "/api/lyte/v2/metrics"
REVISION = "c" * 40


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("LYTE_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    monkeypatch.setenv("LYTE_DEMO_MODE", "true")
    monkeypatch.setenv("LYTE_SOURCE_REVISION", REVISION)
    monkeypatch.setenv("LYTE_REQUIRE_SOURCE_BINDING", "true")
    for key in (
        "SOURCE_REVISION", "GITHUB_SHA", "LYTE_DEV_AUTH_ENABLED",
        "LYTE_DEV_AUTH_TOKEN", "LYTE_WEBHOOK_HMAC_SECRET",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(routes_health, "_ROOT", tmp_path)
    with TestClient(create_app()) as actual:
        yield actual


def samples(response):
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert response.text.endswith("\n")
    return [sample for family in text_string_to_metric_families(response.text) for sample in family.samples]


def test_application_alias_has_real_source_identity_and_preserves_local_route(client):
    assert client.get("/readyz").status_code == 200
    public = client.get(PUBLIC_METRICS)
    local = client.get("/metrics")
    assert public.content == local.content
    observed = samples(public)
    build = [sample for sample in observed if sample.name == "lyte_build_info"]
    assert len(build) == 1
    assert build[0].labels == {"version": __version__, "revision": REVISION}
    assert build[0].value == 1
    assert [s.value for s in observed if s.name == "lyte_db_pool_healthy"] == [1]
    assert public.headers["cache-control"] == "no-store"
    assert public.headers["x-content-type-options"] == "nosniff"


def test_alias_retains_warm_histograms_and_string_labels(client):
    runtime = client.app.state.runtime
    runtime.metrics.record_query("public-metrics", "success", 0.25)
    runtime.metrics.record_connector("github", "success", 0.5)
    observed = samples(client.get(PUBLIC_METRICS))
    buckets = [s for s in observed if s.name.endswith("_bucket")]
    assert buckets
    assert all(isinstance(v, str) for s in observed for v in s.labels.values())
    assert any(s.name == "lyte_query_duration_seconds_count" and s.value == 1 for s in observed)
    assert any(s.name == "lyte_connector_duration_seconds_count" and s.value == 1 for s in observed)


def test_alias_reports_failed_database_probe_without_inventing_health(client, monkeypatch):
    @contextmanager
    def unavailable():
        raise RuntimeError("private database detail")
        yield  # pragma: no cover - context manager never admits a session

    monkeypatch.setattr(client.app.state.runtime.database, "session", unavailable)
    assert client.get("/readyz").status_code == 503
    response = client.get(PUBLIC_METRICS)
    observed = samples(response)
    assert [s.value for s in observed if s.name == "lyte_db_pool_healthy"] == [0]
    assert "private database detail" not in response.text


def test_alias_does_not_mint_receipts_or_authorize_mutation(client):
    before = client.get("/api/lyte/v2/receipts").json()["count"]
    samples(client.get(PUBLIC_METRICS))
    assert client.post(PUBLIC_METRICS, json={}).status_code == 405
    denied = client.post("/api/lyte/v2/analyze", json={})
    assert denied.status_code == 503
    assert client.get("/api/lyte/v2/receipts").json()["count"] == before
    assert client.app.state.runtime.effectors_enabled is False
