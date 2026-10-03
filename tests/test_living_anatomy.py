from __future__ import annotations

from itertools import pairwise

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, update
from sqlalchemy.exc import IntegrityError, OperationalError

from lyte.api import routes_analysis
from lyte.app import create_app
from lyte.domain import TruthLabel
from lyte.intelligence.living_anatomy import build_analysis_anatomy
from lyte.persistence import (
    STREAM_ANATOMY,
    STREAM_IDEMPOTENCY,
    STREAM_MEMORY,
    STREAM_RECEIPTS,
)
from lyte.persistence.models import (
    AnatomyTraceRecord,
    IdempotencyRecord,
    MemoryRecord,
    ReceiptRecord,
)

TENANT_ID = "11111111-1111-4111-8111-111111111111"
WORKSPACE_ID = "22222222-2222-4222-8222-222222222222"
DEV_TOKEN = "living-anatomy-test-token-" + "x" * 40
SOURCE_REVISION = "a" * 40


def _environment(monkeypatch: object) -> None:
    monkeypatch.setenv("LYTE_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    monkeypatch.setenv("LYTE_DEMO_MODE", "true")
    monkeypatch.setenv("LYTE_DEV_AUTH_ENABLED", "true")
    monkeypatch.setenv("LYTE_DEV_AUTH_TOKEN", DEV_TOKEN)
    monkeypatch.setenv("LYTE_DEV_TENANT_ID", TENANT_ID)
    monkeypatch.setenv("LYTE_DEV_WORKSPACE_ID", WORKSPACE_ID)
    monkeypatch.setenv("LYTE_SOURCE_REVISION", SOURCE_REVISION)
    for name in (
        "SOURCE_REVISION",
        "GITHUB_SHA",
        "LYTE_REQUIRE_SOURCE_BINDING",
        "LYTE_REQUIRE_BUILD_RECEIPT",
        "SPACE_ID",
    ):
        monkeypatch.delenv(name, raising=False)


def _headers(key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {DEV_TOKEN}",
        "X-Lyte-Tenant-ID": TENANT_ID,
        "X-Lyte-Workspace-ID": WORKSPACE_ID,
        "Idempotency-Key": key,
    }


def _payload(evidence_ref: str, *, service_id: str = "orders-api") -> dict[str, object]:
    return {
        "service_id": service_id,
        "good_events": 99_500,
        "total_events": 100_000,
        "slo_target": 0.999,
        "requests": 100_000,
        "window_seconds": 300.0,
        "failed_changes": 1,
        "total_changes": 20,
        "cost_usd": 200.0,
        "successful_outcomes": 99_500,
        "revenue_volume": 100_000,
        "baseline_conversion_rate": 0.72,
        "observed_conversion_rate": 0.70,
        "average_order_value": 84.0,
        "currency": "USD",
        "evidence_refs": [evidence_ref],
        "input_truth_label": "SAMPLE",
    }


def _stream_counts(runtime: object) -> dict[str, int]:
    scope = runtime.demo_scope
    return {
        stream: runtime.store.verify_chain(scope, stream).record_count
        for stream in (
            STREAM_RECEIPTS,
            STREAM_IDEMPOTENCY,
            STREAM_MEMORY,
            STREAM_ANATOMY,
        )
    }


def test_analyze_persists_stage_specific_atomic_idempotent_anatomy(monkeypatch) -> None:
    _environment(monkeypatch)
    with TestClient(create_app()) as client:
        runtime = client.app.state.runtime
        evidence_ref = runtime.demo_seed["receipt_hash"]
        payload = _payload(evidence_ref)
        before = _stream_counts(runtime)

        first = client.post(
            "/api/lyte/v2/analyze",
            headers=_headers("living-anatomy-analysis-0001"),
            json=payload,
        )
        assert first.status_code == 200, first.text
        body = first.json()
        assert body["decision"] == "REVIEW"
        assert body["human_approval_required"] is True
        assert body["can_authorize"] is False
        assert body["can_execute"] is False
        assert body["effectors_enabled"] is False
        assert body["causality_claimed"] is False
        assert body["anatomy_persisted"] is True
        assert body["memory_persisted"] is True
        assert body["idempotent_replay"] is False

        stages = body["anatomy_trace"]
        assert [stage["stage"] for stage in stages] == [
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
        assert [stage["state"] for stage in stages] == [
            "OBSERVED",
            "VALIDATED",
            "BOUND",
            "CALCULATED",
            "ENFORCED",
            "REVIEW",
            "PARTIAL",
            "PERSISTED",
            "APPENDED",
        ]
        assert all(stage["execution_state"] == "COMPLETED" for stage in stages)
        assert len({stage["basis_sha256"] for stage in stages}) == 9
        assert len({stage["output_sha256"] for stage in stages}) == 9
        assert all(len(stage["basis_sha256"]) == 64 for stage in stages)
        assert all(len(stage["output_sha256"]) == 64 for stage in stages)
        assert all(stage["source_revision"] == SOURCE_REVISION for stage in stages)
        assert all(stage["trace_id"] == body["trace_id"] for stage in stages)
        for previous, current in pairwise(stages):
            assert current["input_sha256"] == previous["output_sha256"]
            assert current["parent_span_id"] == previous["span_id"]
        assert stages[0]["parent_span_id"] is None

        read_headers = _headers("ignored")
        del read_headers["Idempotency-Key"]
        receipt = client.get(
            f"/api/lyte/v2/receipts/{body['receipt_id']}",
            headers=read_headers,
        )
        assert receipt.status_code == 200, receipt.text
        assert receipt.json()["payload"]["trace_id"] == body["trace_id"]
        assert receipt.json()["payload"]["human_approval_required"] is True
        memory_page = runtime.store.list_memory(
            runtime.demo_scope,
            receipt_hash=body["receipt_id"],
        )
        assert len(memory_page.items) == 1
        memory = memory_page.items[0]
        assert memory.record_hash == body["memory_record_hash"]
        assert memory.metadata_json["trace_id"] == body["trace_id"]
        assert memory.metadata_json["approved_knowledge"] is False
        assert body["receipt_id"] in memory.evidence_refs

        after_first = _stream_counts(runtime)
        assert after_first == {
            STREAM_RECEIPTS: before[STREAM_RECEIPTS] + 1,
            STREAM_IDEMPOTENCY: before[STREAM_IDEMPOTENCY] + 1,
            STREAM_MEMORY: before[STREAM_MEMORY] + 1,
            STREAM_ANATOMY: before[STREAM_ANATOMY] + 9,
        }
        assert runtime.metrics.registry.get_sample_value(
            "lyte_receipts_total", {"kind": "analysis.completed"}
        ) == 1.0
        for path in (
            "/api/lyte/v2/anatomy",
            "/api/lyte/v2/receipts",
            "/api/lyte/v2/catalog",
        ):
            assert client.get(path).status_code == 200
        assert _stream_counts(runtime) == after_first
        replay = client.post(
            "/api/lyte/v2/analyze",
            headers=_headers("living-anatomy-analysis-0001"),
            json=payload,
        )
        assert replay.status_code == 200, replay.text
        assert replay.json()["idempotent_replay"] is True
        assert replay.json()["receipt_id"] == body["receipt_id"]
        assert replay.json()["memory_record_hash"] == body["memory_record_hash"]
        assert replay.json()["trace_id"] == body["trace_id"]
        assert _stream_counts(runtime) == after_first
        assert runtime.metrics.registry.get_sample_value(
            "lyte_receipts_total", {"kind": "analysis.completed"}
        ) == 1.0

        conflict = client.post(
            "/api/lyte/v2/analyze",
            headers=_headers("living-anatomy-analysis-0001"),
            json=_payload(evidence_ref, service_id="payments-api"),
        )
        assert conflict.status_code == 409
        assert conflict.json()["detail"] == (
            "Idempotency-Key was already used for a different request"
        )
        assert _stream_counts(runtime) == after_first
        assert all(
            runtime.store.verify_chain(runtime.demo_scope, stream).valid
            for stream in (
                STREAM_RECEIPTS,
                STREAM_IDEMPOTENCY,
                STREAM_MEMORY,
                STREAM_ANATOMY,
            )
        )


@pytest.mark.parametrize("corruption", ("trace", "memory", "receipt", "idempotency"))
def test_analysis_replay_rejects_corrupted_projected_fields(monkeypatch, corruption) -> None:
    _environment(monkeypatch)
    with TestClient(create_app()) as client:
        runtime = client.app.state.runtime
        payload = _payload(runtime.demo_seed["receipt_hash"])
        headers = _headers("anatomy-projection-corruption-0001")
        first = client.post("/api/lyte/v2/analyze", headers=headers, json=payload)
        assert first.status_code == 200, first.text
        body = first.json()
        before = _stream_counts(runtime)
        if corruption == "trace":
            statement = (
                update(AnatomyTraceRecord)
                .where(AnatomyTraceRecord.span_id == body["anatomy_trace"][-1]["span_id"])
                .values(output_sha256="c" * 64)
            )
        elif corruption == "memory":
            statement = (
                update(MemoryRecord)
                .where(MemoryRecord.record_hash == body["memory_record_hash"])
                .values(summary="Altered materialized summary")
            )
        elif corruption == "receipt":
            statement = (
                update(ReceiptRecord)
                .where(ReceiptRecord.record_hash == body["receipt_id"])
                .values(payload_sha256="c" * 64)
            )
        else:
            statement = (
                update(IdempotencyRecord)
                .where(IdempotencyRecord.response_receipt_hash == body["receipt_id"])
                .values(request_digest="c" * 64)
            )
        # Core SQL deliberately bypasses ORM immutability to model a damaged
        # projection while preserving its original canonical basis and hash.
        with runtime.database.session() as session, session.begin():
            session.execute(statement)
        replay = client.post("/api/lyte/v2/analyze", headers=headers, json=payload)
        assert replay.status_code == 503, replay.text
        assert replay.json()["detail"] == "analysis durable bundle could not be committed"
        assert _stream_counts(runtime) == before


def test_analysis_replay_rejects_current_formula_drift(monkeypatch) -> None:
    _environment(monkeypatch)
    with TestClient(create_app()) as client:
        runtime = client.app.state.runtime
        payload = _payload(runtime.demo_seed["receipt_hash"])
        headers = _headers("anatomy-formula-drift-0001")
        first = client.post("/api/lyte/v2/analyze", headers=headers, json=payload)
        assert first.status_code == 200, first.text
        before = _stream_counts(runtime)
        original = routes_analysis._serialize_formula

        def changed_formula(result, input_label):
            row = original(result, input_label)
            if row["name"] == "availability_sli":
                row["value"] = 0.5
            return row

        monkeypatch.setattr(routes_analysis, "_serialize_formula", changed_formula)
        replay = client.post("/api/lyte/v2/analyze", headers=headers, json=payload)
        assert replay.status_code == 503, replay.text
        assert replay.json()["detail"] == (
            "analysis output differs from its original durable receipt"
        )
        assert _stream_counts(runtime) == before
        assert runtime.metrics.registry.get_sample_value(
            "lyte_receipts_total", {"kind": "analysis.completed"}
        ) == 1.0


@pytest.mark.parametrize("database_error", (OperationalError, IntegrityError))
def test_analysis_bundle_rolls_back_every_stream_on_trace_write_failure(
    monkeypatch, database_error
) -> None:
    _environment(monkeypatch)
    with TestClient(create_app()) as client:
        runtime = client.app.state.runtime
        payload = _payload(runtime.demo_seed["receipt_hash"])
        headers = _headers("anatomy-rollback-injection-0001")
        before = _stream_counts(runtime)
        injected = False

        def fail_trace_insert(
            _connection, _cursor, statement, _parameters, _context, _executemany
        ):
            nonlocal injected
            if statement.upper().startswith("INSERT INTO LYTE_ANATOMY_TRACES") and not injected:
                injected = True
                raise database_error(statement, {}, RuntimeError("injected trace write failure"))

        event.listen(runtime.database.engine, "before_cursor_execute", fail_trace_insert)
        try:
            failed = client.post("/api/lyte/v2/analyze", headers=headers, json=payload)
        finally:
            event.remove(runtime.database.engine, "before_cursor_execute", fail_trace_insert)
        assert injected is True
        assert failed.status_code == 503, failed.text
        assert failed.json()["detail"] == "analysis durable bundle could not be committed"
        assert _stream_counts(runtime) == before
        assert runtime.metrics.registry.get_sample_value(
            "lyte_receipts_total", {"kind": "analysis.completed"}
        ) is None
        retry = client.post("/api/lyte/v2/analyze", headers=headers, json=payload)
        assert retry.status_code == 200, retry.text
        assert retry.json()["idempotent_replay"] is False
        assert _stream_counts(runtime) == {
            STREAM_RECEIPTS: before[STREAM_RECEIPTS] + 1,
            STREAM_IDEMPOTENCY: before[STREAM_IDEMPOTENCY] + 1,
            STREAM_MEMORY: before[STREAM_MEMORY] + 1,
            STREAM_ANATOMY: before[STREAM_ANATOMY] + 9,
        }


def test_analysis_anatomy_rejects_generic_complete_stage_claims() -> None:
    states = (
        "OBSERVED", "VALIDATED", "BOUND", "CALCULATED", "ENFORCED",
        "REVIEW", "PARTIAL", "PERSISTED", "APPENDED",
    )
    stage_results = tuple((state, {}) for state in states)
    with pytest.raises(ValueError, match="invalid state for analysis stage sense"):
        build_analysis_anatomy(
            input_sha256="a" * 64,
            truth_label=TruthLabel.SAMPLE,
            source_revision=SOURCE_REVISION,
            stage_results=(("COMPLETE", {}), *stage_results[1:]),
        )
