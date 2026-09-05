"""Complete Lyte Enterprise runtime, memory, UI, and boundary tests."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_STATE = tempfile.TemporaryDirectory()
os.environ["LYTE_STATE_PATH"] = str(Path(_STATE.name) / "lyte.sqlite3")
os.environ["LYTE_SOURCE_REVISION"] = "7" * 40

from lyte_engine import (  # noqa: E402
    analyze_journey,
    analyze_window,
    github_workflow_observation,
)
import space.server as runtime  # noqa: E402
from space.server import app  # noqa: E402

SESSION_A = "lyte-enterprise-session-a-01234567890123456789"
SESSION_B = "lyte-enterprise-session-b-01234567890123456789"
CLIENT_A = TestClient(app, headers={"X-SZL-Session": SESSION_A})
CLIENT_B = TestClient(app, headers={"X-SZL-Session": SESSION_B})


def test_engine_imports_and_source_bound_readiness_closes() -> None:
    health = CLIENT_A.get("/healthz")
    assert health.status_code == 200
    body = health.json()
    assert body["ok"] is True
    assert body["service"] == "lyte-signal-lattice"
    assert body["version"] == "3.0.0"
    assert body["engine_imported"] is True
    assert body["effectors_enabled"] is False
    assert body["lambda_status"] == "Conjecture 1 (advisory only)"

    ready = CLIENT_A.get("/readyz")
    assert ready.status_code == 200
    assert ready.json()["ready"] is True
    assert ready.json()["build"] == {
        "state": "OBSERVED",
        "revision": "7" * 40,
    }

    expected_identity = {
        "source_repository": "szl-holdings/lyte-services",
        "source_revision": "7" * 40,
        "runtime_repository": "szl-holdings/lyte-services",
        "runtime_source_revision": "7" * 40,
        "effectors_enabled": False,
        "human_approval_required": True,
    }
    for route in ("/healthz", "/api/build-info", "/api/source"):
        response = CLIENT_A.get(route)
        assert response.status_code == 200
        payload = response.json()
        assert {key: payload[key] for key in expected_identity} == expected_identity

    build = CLIENT_A.get("/api/build-info").json()
    assert build["source_binding"]["bindings_agree"] is True
    assert build["receipt_minted"] is False


def test_runtime_identity_fails_readiness_closed_on_source_mismatch(monkeypatch) -> None:
    monkeypatch.setattr(
        runtime,
        "_source_observation",
        lambda: {
            "state": "MISMATCH",
            "revision": "6" * 40,
            "bindings_agree": False,
            "evidence_sources": ["container-file", "env:LYTE_SOURCE_REVISION"],
        },
    )
    health = CLIENT_A.get("/healthz")
    assert health.status_code == 200
    assert health.json()["ok"] is False
    assert health.json()["source_revision"] == "UNAVAILABLE"
    assert health.json()["runtime_source_revision"] == "UNAVAILABLE"
    ready = CLIENT_A.get("/readyz")
    assert ready.status_code == 503
    assert ready.json()["ready"] is False
    assert ready.json()["build"]["revision"] == "UNAVAILABLE"
    assert ready.json()["source_revision"] == "UNAVAILABLE"

    build = CLIENT_A.get("/api/source")
    assert build.status_code == 200
    assert build.json()["build"]["revision"] == "UNAVAILABLE"
    assert build.json()["source_revision"] == "UNAVAILABLE"


def test_public_catalog_anatomy_formulas_sources_and_metrics() -> None:
    catalog = CLIENT_A.get("/api/lyte/v3/catalog").json()
    assert len(catalog["lenses"]) == 6
    assert catalog["positioning"]["category"] == "GOVERNED_ENTERPRISE_OBSERVABILITY"
    assert catalog["effectors_enabled"] is False

    capabilities = CLIENT_A.get("/api/lyte/v3/capabilities").json()
    assert capabilities["service_observability"] is True
    assert capabilities["customer_journey_intelligence"] is True
    assert capabilities["ai_agent_operations"] is True
    assert capabilities["wire_compatible_otlp_collector"] is False
    assert capabilities["automatic_remediation"] is False

    anatomy = CLIENT_A.get("/api/lyte/v3/anatomy").json()
    assert [item["id"] for item in anatomy["organs"]] == [
        "sense",
        "normalize",
        "context",
        "formula",
        "policy",
        "decide",
        "verify",
        "remember",
        "receipt",
    ]

    formulas = CLIENT_A.get("/api/lyte/v3/formulas").json()
    assert formulas["count"] >= 8
    assert "Conjecture 1" in formulas["lambda_status"]
    assert formulas["can_authorize"] is False
    assert formulas["can_be_sole_allow_basis"] is False

    sources = CLIENT_A.get("/api/lyte/v3/sources").json()
    assert {item["id"] for item in sources["sources"]} == {
        "github-actions",
        "otel-shaped-json",
        "atlas-business-event",
    }
    assert sources["caller_supplied_fetch_urls_allowed"] is False

    metrics = CLIENT_A.get("/metrics")
    assert metrics.status_code == 200
    assert "lyte_http_requests_total" in metrics.text
    assert "lyte_observations" in metrics.text


def test_enterprise_scenario_is_interactive_but_explicitly_sample() -> None:
    response = CLIENT_A.get("/api/lyte/v3/scenario")
    assert response.status_code == 200
    body = response.json()
    assert body["demo_mode"] is True
    assert body["truth_label"] == "SAMPLE"
    assert body["action_execution"] == "DISABLED"
    assert body["effectors_enabled"] is False
    assert len(body["analysis"]["lenses"]) == 6
    assert len(body["analysis"]["services"]) == 3
    assert body["analysis"]["enterprise_state"]["revenue_at_risk_usd"] > 0
    assert body["journey"]["revenue_at_risk_usd"] > 0
    assert body["otel"]["sensitive_attributes_removed"] is True
    assert body["atlas"]["causality_claimed"] is False
    assert len(body["playback"]) == 4


def _run_sample(client: TestClient) -> tuple[dict, dict]:
    scenario = client.get("/api/lyte/v3/scenario").json()
    analysis = client.post(
        "/api/lyte/v3/analyze",
        json=scenario["inputs"]["analysis"],
    )
    assert analysis.status_code == 200, analysis.text
    journey = client.post(
        "/api/lyte/v3/journeys/analyze",
        json=scenario["inputs"]["journey"],
    )
    assert journey.status_code == 200, journey.text
    return analysis.json(), journey.json()


def test_analysis_and_journey_mint_session_scoped_receipts() -> None:
    analysis, journey = _run_sample(CLIENT_A)
    for body in (analysis, journey):
        receipt = body["receipt"]
        assert len(receipt["receipt_id"]) == 64
        assert receipt["session_scope_sha256"] != SESSION_A
        assert receipt["raw_session_token_recorded"] is False
        assert receipt["effectors_enabled"] is False
        assert SESSION_A not in json.dumps(body)

    result = analysis["analysis"]
    assert len(result["lenses"]) == 6
    assert result["service_outcome_graph"]["causality_claimed"] is False
    assert result["enterprise_state"]["queue_items"] >= 1
    assert result["agent_traces"]["prompt_content_recorded"] is False
    assert result["agent_traces"]["response_content_recorded"] is False

    journey_result = journey["journey"]
    assert journey_result["human_approval_required"] is True
    assert journey_result["effectors_enabled"] is False
    assert journey_result["revenue_at_risk_usd"] > 0


def test_second_brain_ask_playback_receipt_lookup_and_isolation() -> None:
    analysis, _ = _run_sample(CLIENT_A)
    receipt_id = analysis["receipt"]["receipt_id"]

    memory_a = CLIENT_A.get("/api/lyte/v3/second-brain")
    assert memory_a.status_code == 200
    assert any(
        item["receipt_id"] == receipt_id
        for item in memory_a.json()["memory"]
    )
    assert memory_a.json()["raw_session_token_recorded"] is False

    memory_b = CLIENT_B.get("/api/lyte/v3/second-brain")
    assert memory_b.status_code == 200
    assert memory_b.json()["memory"] == []

    lookup_a = CLIENT_A.get(f"/api/lyte/v3/receipts/{receipt_id}")
    assert lookup_a.status_code == 200
    lookup_b = CLIENT_B.get(f"/api/lyte/v3/receipts/{receipt_id}")
    assert lookup_b.status_code == 404

    ask = CLIENT_A.post(
        "/api/lyte/v3/ask",
        json={"question": "Which service is consuming the most error budget?"},
    )
    assert ask.status_code == 200
    answer = ask.json()
    assert answer["truth_label"] == "MEASURED"
    assert answer["evidence_receipt_ids"]
    assert answer["causality_claimed"] is False
    assert answer["effectors_enabled"] is False

    playback = CLIENT_A.get("/api/lyte/v3/playback")
    assert playback.status_code == 200
    assert playback.json()["count"] >= 2
    assert playback.json()["causality_claimed"] is False


def test_hatun_reviews_evidence_but_denies_effectors() -> None:
    analysis, _ = _run_sample(CLIENT_A)
    receipt_id = analysis["receipt"]["receipt_id"]

    review = CLIENT_A.post(
        "/api/lyte/v3/hatun/evaluate",
        json={
            "intent": "review checkout degradation evidence",
            "requested_action": "incident.review",
            "axes": {
                "evidence": 0.96,
                "safety": 0.94,
                "policy": 0.95,
                "reversibility": 0.92,
            },
            "evidence_receipt_ids": [receipt_id],
        },
    )
    assert review.status_code == 200
    body = review.json()
    assert body["decision"] == "REVIEW"
    assert body["can_authorize"] is False
    assert body["can_execute"] is False
    assert body["effectors_enabled"] is False
    assert body["session_token_recorded"] is False
    assert body["credential_material_recorded"] is False
    assert body["lambda_status"] == "CONJECTURE_1_ADVISORY"

    denied = CLIENT_A.post(
        "/api/lyte/v3/hatun/evaluate",
        json={
            "intent": "attempt an unattended change",
            "requested_action": "service.execute",
            "axes": {
                "evidence": 0.99,
                "safety": 0.99,
                "policy": 0.99,
            },
            "evidence_receipt_ids": [receipt_id],
        },
    )
    assert denied.status_code == 200
    assert denied.json()["decision"] == "DENY"
    assert "REQUESTED_ACTION_REQUIRES_DISABLED_EFFECTOR" in denied.json()["blockers"]


def test_otel_and_atlas_ingest_remove_sensitive_content() -> None:
    otel = CLIENT_A.post(
        "/api/lyte/v3/telemetry/otel",
        json={
            "spans": [
                {
                    "trace_id": "trace-a",
                    "span_id": "root",
                    "service": "checkout",
                    "name": "POST /checkout",
                    "duration_ms": 121,
                    "status": "OK",
                    "attributes": {
                        "authorization": "must-not-survive",
                        "prompt": "must-not-survive",
                        "deployment.id": "742",
                    },
                }
            ]
        },
    )
    assert otel.status_code == 200, otel.text
    observation = otel.json()["observation"]
    assert observation["sensitive_attributes_removed"] is True
    serialized = json.dumps(observation)
    assert "must-not-survive" not in serialized

    atlas = CLIENT_A.post(
        "/api/lyte/v3/telemetry/atlas",
        json={
            "events": [
                {
                    "event_id": "risk-1",
                    "event_class": "business.risk.detected",
                    "domain": "commerce",
                    "severity": "high",
                    "timestamp": 1788541200000,
                    "business_value": {
                        "amount": 1000,
                        "currency": "USD",
                        "type": "at-risk",
                    },
                    "slo_impact": {
                        "impact": "breached",
                        "slo_id": "checkout",
                    },
                }
            ]
        },
    )
    assert atlas.status_code == 200, atlas.text
    assert atlas.json()["observation"]["business_value"]["total_usd"] == 1000
    assert atlas.json()["observation"]["effectors_enabled"] is False


def test_github_source_connector_is_fixed_bounded_and_receipted() -> None:
    payload = {
        "workflow_runs": [
            {
                "id": 1,
                "name": "enterprise",
                "display_title": "CI",
                "head_branch": "main",
                "head_sha": "a" * 40,
                "event": "push",
                "status": "completed",
                "conclusion": "success",
                "run_number": 7,
                "created_at": "2026-09-04T10:00:00Z",
                "run_started_at": "2026-09-04T10:00:05Z",
                "updated_at": "2026-09-04T10:01:05Z",
                "html_url": "https://github.com/szl-holdings/lyte-services/actions/runs/1",
            }
        ]
    }

    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=json.dumps(payload).encode(),
        )
    )
    observation, receipt = github_workflow_observation(
        "lyte-services",
        limit=10,
        transport=transport,
    )
    assert observation["repository"] == "szl-holdings/lyte-services"
    assert observation["success_rate"] == 1.0
    assert observation["p50_duration_seconds"] == 60.0
    assert receipt["source_url"].startswith(
        "https://api.github.com/repos/szl-holdings/lyte-services/actions/runs"
    )
    assert receipt["raw_session_token_recorded"] is False

    with pytest.raises(ValueError, match="allowlist"):
        github_workflow_observation("not-a-real-repository", transport=transport)


def test_formula_boundaries_and_validation_fail_closed() -> None:
    with pytest.raises(ValueError):
        analyze_window({"services": [], "outcomes": [], "traces": []})
    with pytest.raises(ValueError):
        analyze_journey({"name": "Checkout", "stages": []})

    oversized = b"x" * 512001
    response = CLIENT_A.post(
        "/api/lyte/v3/analyze",
        content=oversized,
        headers={
            "Content-Type": "application/json",
            "Content-Length": str(len(oversized)),
            "X-SZL-Session": SESSION_A,
        },
    )
    assert response.status_code == 413

    anonymous = TestClient(app)
    assert anonymous.get("/api/lyte/v3/second-brain").status_code == 422
    invalid = anonymous.get(
        "/api/lyte/v3/second-brain",
        headers={"X-SZL-Session": "short"},
    )
    assert invalid.status_code == 400


def test_legacy_compiler_remains_compatible_and_actuation_stays_blocked() -> None:
    cells = CLIENT_A.get("/api/cells")
    assert cells.status_code == 200
    assert any(item["id"] == "lyte" for item in cells.json())

    compiled = CLIENT_A.post(
        "/api/compile",
        json={"cell": "lyte", "signal": "enterprise window"},
    )
    assert compiled.status_code == 200
    assert compiled.json()["decision"] == "ALLOW"

    acted = CLIENT_A.post(
        "/api/act",
        json={"cell": "lyte", "payload": {"operation": "restart"}},
    )
    assert acted.status_code == 200
    assert acted.json()["decision"] == "BLOCKED"
    assert acted.json()["can_execute"] is False
    assert acted.json()["effectors_enabled"] is False


def test_front_door_is_original_responsive_accessible_and_local_asset_only() -> None:
    response = CLIENT_A.get("/")
    assert response.status_code == 200
    text = response.text
    for fragment in (
        'data-lyte="signal-lattice-v3"',
        "Lyte Enterprise",
        "SIGNAL LATTICE",
        "See change.",
        "Act with proof.",
        "Revenue at risk",
        "AI AGENT OPERATIONS",
        "INCIDENT PLAYBACK",
        "ASK LYTE",
        "viewport-fit=cover",
        "--touch:44px",
        "@media(pointer:coarse)",
        "@media(prefers-reduced-motion:reduce)",
        "@media(forced-colors:active)",
        "@media(prefers-contrast:more)",
        ":focus-visible",
        "overflow-x:clip",
        "SAMPLE / MODELED",
        "EFFECTORS DISABLED",
    ):
        assert fragment in text
    lowered = text.casefold()
    for forbidden in (
        "<script src=",
        "<link rel=",
        "localstorage",
        "sessionstorage",
        "document.cookie",
        "newrelic",
        "datadog",
    ):
        assert forbidden not in lowered


def test_no_missing_enterprise_modules_or_old_organs_import() -> None:
    required = [
        ROOT / "lyte_engine" / "memory.py",
        ROOT / "space" / "__init__.py",
        ROOT / "requirements.txt",
        ROOT / "requirements-test.txt",
    ]
    assert all(path.is_file() for path in required)
    server = (ROOT / "space" / "server.py").read_text(encoding="utf-8")
    init = (ROOT / "lyte_engine" / "__init__.py").read_text(encoding="utf-8")
    assert "a11oy_factory.organs" not in server
    assert "from .memory import" in init
