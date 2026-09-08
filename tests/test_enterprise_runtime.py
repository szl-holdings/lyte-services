"""Compatibility boundaries retained while the canonical v4 runtime evolves."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from lyte.app import app as canonical_app
from lyte.app import create_app as canonical_create_app
from lyte.app import run as canonical_run
from lyte_api import AnalyzeRequest, AskRequest, CompileRequest, HatunRequest, JourneyRequest
from lyte_engine import SessionLedger, observation_receipt, session_scope
from space.server import app as compatibility_app
from space.server import create_app as compatibility_create_app
from space.server import run as compatibility_run


def test_space_server_is_only_a_canonical_runtime_alias() -> None:
    assert compatibility_app is canonical_app
    assert compatibility_create_app is canonical_create_app
    assert compatibility_run is canonical_run

    source = (Path(__file__).resolve().parents[1] / "space" / "server.py").read_text(
        encoding="utf-8"
    )
    assert "FastAPI(" not in source
    assert "X-SZL-Session" not in source
    assert "from lyte.app import app, create_app, run" in source


def test_canonical_runtime_exposes_v2_without_legacy_mutation_surfaces() -> None:
    route_paths = set(canonical_app.openapi()["paths"])
    assert {
        "/healthz",
        "/readyz",
        "/api/build-info",
        "/api/source",
        "/api/lyte/v2/catalog",
        "/api/lyte/v2/analyze",
        "/api/lyte/v2/ask",
        "/api/lyte/v2/hatun/evaluate",
        "/api/lyte/v2/ingest/otlp",
    } <= route_paths
    assert "/api/act" not in route_paths
    assert "/api/compile" not in route_paths
    assert not any(path.startswith("/api/lyte/v3") for path in route_paths)


def test_legacy_request_models_keep_normalization_and_fail_closed_validation() -> None:
    assert AskRequest(question="  what   changed? ").question == "what changed?"
    assert CompileRequest(cell="lyte", prev_hash="A" * 64).prev_hash == "a" * 64

    with pytest.raises(ValidationError):
        AskRequest(question="valid", unexpected=True)
    with pytest.raises(ValidationError):
        JourneyRequest(name="   ", stages=[{"id": "checkout"}])
    with pytest.raises(ValidationError):
        AnalyzeRequest(
            services=[{"name": "checkout"}],
            evidence_refs=[" duplicate ", "duplicate"],
        )
    with pytest.raises(ValidationError):
        HatunRequest(
            intent="review",
            requested_action="invalid action",
            axes={"availability": 0.9, "risk": 0.2},
        )
    with pytest.raises(ValidationError):
        HatunRequest(
            intent="review",
            axes={"availability": float("nan"), "risk": 0.2},
        )


def test_legacy_ledger_remains_durable_scoped_and_idempotent(tmp_path: Path) -> None:
    caller_session = "compatibility-session-token-0123456789"
    other_session = "other-compatibility-token-0123456789"
    scope = session_scope(caller_session)
    other_scope = session_scope(other_session)
    path = tmp_path / "legacy-compatibility.sqlite3"
    receipt = observation_receipt(
        scope=scope,
        kind="compatibility-test",
        payload={"value": 1},
        truth_label="MEASURED",
        source_url="https://example.invalid/evidence/1",
        observed_at=123.0,
    )

    first = SessionLedger(path)
    first.append(
        scope,
        kind="compatibility-test",
        receipt=receipt,
        summary={"value": 1},
    )
    first.append(
        scope,
        kind="compatibility-test",
        receipt=receipt,
        summary={"value": 999},
    )

    reopened = SessionLedger(path)
    records = reopened.recent(scope)
    assert len(records) == 1
    assert records[0]["receipt_id"] == receipt["receipt_id"]
    assert records[0]["summary"] == {"value": 1}
    assert reopened.get(scope, receipt["receipt_id"]) == records[0]
    assert reopened.receipt_ids(scope) == {receipt["receipt_id"]}
    assert reopened.recent(other_scope) == []
    assert reopened.get(other_scope, receipt["receipt_id"]) is None
    assert reopened.status()["durability"] == "SQLITE_APPEND_ONLY"
    assert caller_session.encode() not in path.read_bytes()


def test_source_less_receipt_form_is_deterministic_and_non_authorizing() -> None:
    scope = session_scope("source-less-compatibility-token-0123456789")
    arguments = {
        "scope": scope,
        "kind": "compatibility-test",
        "payload": {"value": 1},
        "truth_label": "MEASURED",
    }
    first = observation_receipt(**arguments)
    second = observation_receipt(**arguments)
    assert first == second
    assert first["source_url"] == ""
    assert first["observed_at"] == 0.0
    assert first["raw_session_token_recorded"] is False
    assert first["effectors_enabled"] is False
