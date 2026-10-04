"""Synthetic fixtures verify enterprise evidence boundaries, not production claims."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from lyte.app import create_app
from lyte.domain import (
    OperationalEntityKind,
    OperationalRecordDraft,
    ReceiptDraft,
    Scope,
    TenantSpec,
    TruthLabel,
    WorkspaceSpec,
)
from lyte.governance import MemoryDraft, MemoryKind, digest_scope_token
from lyte.intelligence.enterprise_ask import EnterpriseAnswer, ask_enterprise
from lyte.persistence import STREAM_MEMORY, STREAM_RECEIPTS, Database, LyteStore

TENANT_ID = "33333333-3333-4333-8333-333333333333"
WORKSPACE_ID = "44444444-4444-4444-8444-444444444444"
DEV_TOKEN = "enterprise-ask-test-token-" + "x" * 40
AT = datetime(2026, 10, 1, 12, tzinfo=UTC)


def _scope(store: LyteStore, slug: str, *, fixed: bool = False) -> Scope:
    tenant = TenantSpec(slug, slug, TENANT_ID if fixed else None)
    store.create_tenant(tenant)
    workspace = WorkspaceSpec(
        tenant.id, "operations", "Operations", WORKSPACE_ID if fixed else None
    )
    store.create_workspace(workspace)
    return Scope(tenant.id, workspace.id)


def _receipt(
    store,
    scope,
    *,
    label=TruthLabel.REPORTED,
    kind="observation.recorded",
    subject="checkout",
    payload=None,
):
    return store.append_receipt(
        scope,
        ReceiptDraft(
            kind=kind,
            subject_type="service",
            subject_id=subject,
            truth_label=label,
            payload=payload or {"fixture": "synthetic"},
            evidence_refs=("fixture:synthetic",),
        ),
        created_at=AT,
    )


def _row(store, scope, kind, entity_id, body, refs, *, label=TruthLabel.REPORTED):
    return store.insert_operational(
        scope,
        OperationalRecordDraft(
            entity_kind=kind,
            entity_id=entity_id,
            name=entity_id,
            body=body,
            truth_label=label,
            evidence_refs=tuple(refs),
            observed_at=AT,
        ),
    )


@pytest.fixture
def store_scope():
    database = Database("sqlite+pysqlite:///:memory:")
    database.create_schema()
    store = LyteStore(database)
    yield store, _scope(store, "enterprise-test")
    database.dispose()


@pytest.fixture
def enterprise_client(monkeypatch):
    monkeypatch.setenv("LYTE_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    monkeypatch.setenv("LYTE_DEMO_MODE", "false")
    monkeypatch.setenv("LYTE_DEV_AUTH_ENABLED", "true")
    monkeypatch.setenv("LYTE_DEV_AUTH_TOKEN", DEV_TOKEN)
    monkeypatch.setenv("LYTE_DEV_TENANT_ID", TENANT_ID)
    monkeypatch.setenv("LYTE_DEV_WORKSPACE_ID", WORKSPACE_ID)
    for name in (
        "LYTE_REQUIRE_SOURCE_BINDING",
        "LYTE_REQUIRE_BUILD_RECEIPT",
        "SPACE_ID",
        "OIDC_ISSUER",
        "OIDC_AUDIENCE",
        "OIDC_JWKS_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    with TestClient(create_app()) as client:
        runtime = client.app.state.runtime
        scope = _scope(runtime.store, "enterprise-api", fixed=True)
        yield client, runtime, scope


def _headers(**extra):
    return {
        "Authorization": f"Bearer {DEV_TOKEN}",
        "X-Lyte-Tenant-ID": TENANT_ID,
        "X-Lyte-Workspace-ID": WORKSPACE_ID,
        **extra,
    }


def _memory(receipt, *, kind=MemoryKind.OBSERVATION, service="checkout", label=TruthLabel.REPORTED):
    return MemoryDraft(
        kind=kind,
        summary="Synthetic scoped observation",
        truth_label=label,
        evidence_refs=(receipt.record_hash,),
        subject_dimensions={"service": (service,), "journey": ("checkout",)},
    )


def _payload(receipt, *, label="REPORTED"):
    return {
        "summary": "Synthetic scoped observation",
        "truth_label": label,
        "evidence_refs": [receipt.record_hash],
        "receipt_hash": receipt.record_hash,
        "subject_dimensions": {"service": ["checkout"]},
    }


@pytest.mark.parametrize(
    ("question", "intent"),
    [
        ("Where is checkout revenue at risk?", "checkout_revenue_risk"),
        ("Which service has highest error budget burn?", "highest_error_budget_burn"),
        ("What changed before the journey degraded?", "pre_journey_change"),
        ("Which agent is expensive and unreliable?", "agent_cost_reliability"),
        ("Which deployment correlates with this incident?", "incident_deployment_correlation"),
        ("What should the operator review first?", "operator_review_priority"),
        ("What evidence and formula explains this?", "evidence_and_formulas"),
    ],
)
def test_supported_queries_are_deterministic_cited_and_read_only(store_scope, question, intent):
    store, scope = store_scope
    receipt = _receipt(store, scope)
    refs = (receipt.record_hash,)
    _row(
        store,
        scope,
        OperationalEntityKind.SERVICE,
        "checkout",
        {
            "indicators": {
                "error_budget_burn": {
                    "value": 3.0,
                    "truth_label": "REPORTED",
                    "metadata": {"formula": "fixture"},
                }
            },
        },
        refs,
    )
    _row(
        store,
        scope,
        OperationalEntityKind.BUSINESS_OUTCOME,
        "checkout-revenue",
        {
            "revenue_at_risk_usd": 80.0,
        },
        refs,
    )
    _row(
        store,
        scope,
        OperationalEntityKind.CUSTOMER_JOURNEY,
        "checkout",
        {
            "state": "DEGRADED",
            "service_ids": ["checkout"],
            "at": "2026-10-01T12:02:00Z",
        },
        refs,
    )
    _row(
        store,
        scope,
        OperationalEntityKind.DEPLOYMENT_EVENT,
        "deployment",
        {
            "service_id": "checkout",
            "at": "2026-10-01T12:00:00Z",
        },
        refs,
    )
    _row(
        store,
        scope,
        OperationalEntityKind.AGENT_TRACE_SUMMARY,
        "support-agent",
        {
            "cost_per_trace_usd": 0.2,
            "tool_failure_rate": 0.1,
        },
        refs,
    )
    _row(
        store,
        scope,
        OperationalEntityKind.INCIDENT,
        "checkout-incident",
        {
            "service_ids": ["checkout"],
            "at": "2026-10-01T12:04:00Z",
        },
        refs,
    )
    _row(
        store,
        scope,
        OperationalEntityKind.RECOMMENDATION,
        "review-checkout",
        {
            "recommended_next_review": "Review the scoped synthetic checkout evidence",
        },
        refs,
    )
    before = store.verify_chain(scope, STREAM_RECEIPTS).record_count
    answer = ask_enterprise(store, scope, question).to_dict()
    assert answer == ask_enterprise(store, scope, question).to_dict()
    assert answer["intent"] == intent
    assert answer["answer"] is not None
    assert next(iter(answer)) == "citations"
    assert answer["citations"] and answer["evidence_receipt_ids"] == [receipt.record_hash]
    assert 0.0 < answer["confidence"] <= 0.97
    assert answer["can_execute"] is False and answer["causality_claimed"] is False
    assert store.verify_chain(scope, STREAM_RECEIPTS).record_count == before


@pytest.mark.parametrize(
    "receipt_label",
    [TruthLabel.SAMPLE, TruthLabel.ROADMAP, TruthLabel.UNAVAILABLE, TruthLabel.MODELED],
)
def test_real_reported_query_rejects_weaker_receipt_truth(store_scope, receipt_label):
    store, scope = store_scope
    receipt = _receipt(store, scope, label=receipt_label)
    _row(
        store,
        scope,
        OperationalEntityKind.SERVICE,
        "checkout",
        {
            "error_budget_burn": 9.0,
        },
        (receipt.record_hash,),
    )
    answer = ask_enterprise(store, scope, "Which service has highest error budget burn?")
    assert answer.truth_label == "UNAVAILABLE" and answer.answer is None
    assert not answer.citations


def test_cross_workspace_and_partial_claim_inputs_cannot_gain_citations(store_scope):
    store, scope = store_scope
    other_scope = _scope(store, "other-enterprise")
    foreign = _receipt(store, other_scope)
    _row(
        store,
        scope,
        OperationalEntityKind.SERVICE,
        "checkout",
        {
            "error_budget_burn": 9.0,
        },
        (foreign.record_hash,),
    )
    assert (
        ask_enterprise(store, scope, "Which service has highest error budget burn?").answer is None
    )
    local = _receipt(store, scope)
    _row(
        store,
        scope,
        OperationalEntityKind.DEPLOYMENT_EVENT,
        "deployment",
        {
            "service_id": "checkout",
            "at": "2026-10-01T12:00:00Z",
        },
        (local.record_hash,),
    )
    _row(
        store,
        scope,
        OperationalEntityKind.CUSTOMER_JOURNEY,
        "checkout",
        {
            "state": "DEGRADED",
            "service_ids": ["checkout"],
            "at": "2026-10-01T12:02:00Z",
        },
        ("source:missing-journey-receipt",),
    )
    partial = ask_enterprise(store, scope, "What changed before the journey degraded?")
    assert partial.answer is None and not partial.citations


def test_sample_quantitative_indicator_cannot_become_a_real_answer(store_scope):
    store, scope = store_scope
    receipt = _receipt(store, scope)
    _row(
        store,
        scope,
        OperationalEntityKind.SERVICE,
        "checkout",
        {
            "indicators": {"error_budget_burn": {"value": 9.0, "truth_label": "SAMPLE"}},
        },
        (receipt.record_hash,),
    )
    assert (
        ask_enterprise(store, scope, "Which service has highest error budget burn?").answer is None
    )


@pytest.mark.parametrize("confidence", [1.0, -0.1, float("nan"), float("inf")])
def test_enterprise_answer_enforces_trust_ceiling(confidence):
    with pytest.raises(ValueError, match="confidence"):
        EnterpriseAnswer("test", "test", None, "UNAVAILABLE", (), (), (), confidence=confidence)


def _execution_fixture(
    store,
    scope,
    *,
    action_state="EXECUTED",
    execution_label=TruthLabel.MEASURED,
    window_start="2026-10-01T12:01:00Z",
    outcome_label=TruthLabel.MEASURED,
):
    execution = _receipt(
        store,
        scope,
        label=execution_label,
        kind="action.executed",
        subject="action-1",
        payload={
            "state": "EXECUTED",
            "simulation": False,
            "executed_at": "2026-10-01T12:00:00Z",
        },
    )
    _row(
        store,
        scope,
        OperationalEntityKind.ACTION_REQUEST,
        "action-1",
        {
            "state": action_state,
            "simulation": False,
            "execution_receipt_hash": execution.record_hash,
        },
        (execution.record_hash,),
        label=TruthLabel.MEASURED,
    )
    outcome = _receipt(
        store,
        scope,
        label=outcome_label,
        kind="outcome.verified",
        subject="outcome-1",
        payload={
            "action_request_id": "action-1",
            "execution_receipt_hash": execution.record_hash,
            "production_outcome_claimed": True,
            "improved": True,
            "window_start": window_start,
            "window_end": "2026-10-01T12:10:00Z",
        },
    )
    _row(
        store,
        scope,
        OperationalEntityKind.OUTCOME_VERIFICATION,
        "outcome-1",
        {
            "state": "IMPROVED",
            "production_outcome_claimed": True,
            "action_request_id": "action-1",
            "execution_receipt_hash": execution.record_hash,
            "outcome_receipt_hash": outcome.record_hash,
            "summary": "Synthetic improvement fixture",
        },
        (outcome.record_hash,),
        label=TruthLabel.MEASURED,
    )
    return execution, outcome


@pytest.mark.parametrize(
    "changes",
    [
        {"action_state": "APPROVED"},
        {"execution_label": TruthLabel.REPORTED},
        {"outcome_label": TruthLabel.REPORTED},
        {"window_start": "2026-10-01T11:00:00Z"},
        {"window_start": "2026-10-01T12:01:00"},
    ],
)
def test_improvement_requires_execution_and_subsequent_measured_outcome(store_scope, changes):
    store, scope = store_scope
    _execution_fixture(store, scope, **changes)
    answer = ask_enterprise(store, scope, "What improved after the approved action?")
    assert answer.answer is None and answer.truth_label == "UNAVAILABLE"


def test_complete_external_execution_evidence_remains_non_authorizing(store_scope):
    store, scope = store_scope
    execution, outcome = _execution_fixture(store, scope)
    answer = ask_enterprise(store, scope, "What improved after the approved action?").to_dict()
    assert answer["answer"] is not None and answer["truth_label"] == "MEASURED"
    assert set(answer["evidence_receipt_ids"]) == {execution.record_hash, outcome.record_hash}
    assert len(answer["citations"]) == 2 and answer["can_execute"] is False
    assert "stored receipt evidence is not independent certification" in answer["limitations"]


def test_memory_filters_partition_and_finite_truncation(store_scope):
    store, scope = store_scope
    receipt = _receipt(store, scope)
    first = store.append_memory(
        scope, _memory(receipt), receipt_hash=receipt.record_hash, created_at=AT
    )
    second = store.append_memory(
        scope,
        _memory(receipt, kind=MemoryKind.APPROVED_KNOWLEDGE),
        receipt_hash=receipt.record_hash,
        created_at=AT + timedelta(minutes=1),
    )
    other_scope = _scope(store, "other-memory")
    other_receipt = _receipt(store, other_scope)
    other = store.append_memory(
        other_scope, _memory(other_receipt), receipt_hash=other_receipt.record_hash
    )
    page = store.list_memory(
        scope,
        kind="OBSERVATION",
        subject_dimensions={"service": "checkout"},
        receipt_hash=receipt.record_hash,
        created_after=AT,
        created_before=AT,
    )
    assert [row.id for row in page.items] == [first.id]
    bounded = store.list_memory(scope, scan_limit=1, limit=1)
    assert [row.id for row in bounded.items] == [second.id]
    assert bounded.scan_truncated is True and bounded.has_more is False
    empty = store.list_memory(scope, scan_limit=1, subject_dimensions={"service": "absent"})
    assert empty.scan_truncated is True and empty.has_more is False and not empty.items
    assert other.id not in {row.id for row in store.list_memory(scope).items}


@pytest.mark.parametrize("field", ["session_digest", "scope_digest", "raw_prompt", "raw_response"])
def test_memory_metadata_cannot_reintroduce_session_or_raw_content(field):
    with pytest.raises(ValueError, match="forbidden in memory"):
        MemoryDraft(
            kind=MemoryKind.OBSERVATION,
            summary="summary",
            truth_label=TruthLabel.REPORTED,
            evidence_refs=("fixture:synthetic",),
            metadata={"nested": [{field: "x" * 64}]},
        )


def test_memory_missing_and_foreign_receipts_are_uniform_client_errors(enterprise_client):
    client, runtime, scope = enterprise_client
    other_scope = _scope(runtime.store, "other-api")
    foreign = _receipt(runtime.store, other_scope)
    before = runtime.store.verify_chain(scope, STREAM_MEMORY).record_count
    foreign_response = client.post(
        "/api/lyte/v2/second-brain/observations", headers=_headers(), json=_payload(foreign)
    )
    missing_payload = _payload(foreign)
    missing_payload.update(receipt_hash="f" * 64, evidence_refs=["f" * 64])
    missing_response = client.post(
        "/api/lyte/v2/second-brain/observations", headers=_headers(), json=missing_payload
    )
    assert missing_response.status_code == foreign_response.status_code == 422
    assert missing_response.json()["detail"] == foreign_response.json()["detail"]
    assert runtime.store.verify_chain(scope, STREAM_MEMORY).record_count == before


def test_memory_sessions_are_transient_gets_are_read_only_and_roles_apply(
    enterprise_client, monkeypatch
):
    client, runtime, scope = enterprise_client
    receipt = _receipt(runtime.store, scope)
    write = client.post(
        "/api/lyte/v2/second-brain/observations", headers=_headers(), json=_payload(receipt)
    )
    assert write.status_code == 201 and write.json()["item"]["record_hash"]
    before = runtime.store.verify_chain(scope, STREAM_RECEIPTS).record_count
    tokens = ("session-A-" + "a" * 48, "session-B-" + "b" * 48)
    pages = [
        client.get(
            "/api/lyte/v2/second-brain?service=checkout",
            headers=_headers(**{"X-Lyte-Session": token}),
        ).json()
        for token in tokens
    ]
    assert pages[0] == pages[1] and pages[0]["count"] == 1
    for row in runtime.store.list_memory(scope).items:
        for token in tokens:
            assert token not in str(row.basis_json)
            assert digest_scope_token(scope, token) not in str(row.basis_json)
    assert runtime.store.verify_chain(scope, STREAM_RECEIPTS).record_count == before
    assert (
        client.get(
            "/api/lyte/v2/second-brain", headers=_headers(**{"X-Lyte-Session": "bad"})
        ).status_code
        == 400
    )
    principal = runtime.authentication.authenticate_header(f"Bearer {DEV_TOKEN}")
    monkeypatch.setattr(
        runtime.authentication,
        "authenticate_header",
        lambda _: replace(principal, roles=frozenset({"operator"})),
    )
    assert (
        client.post(
            "/api/lyte/v2/second-brain/approved-knowledge",
            headers=_headers(),
            json=_payload(receipt),
        ).status_code
        == 403
    )
    monkeypatch.setattr(
        runtime.authentication,
        "authenticate_header",
        lambda _: replace(principal, roles=frozenset({"viewer"})),
    )
    assert (
        client.post(
            "/api/lyte/v2/second-brain/observations", headers=_headers(), json=_payload(receipt)
        ).status_code
        == 403
    )


@pytest.mark.parametrize(
    "query",
    [
        "from=2026-10-01T12:00:00",
        "from=2026-10-02T12:00:00Z&to=2026-10-01T12:00:00Z",
        "offset=-1",
        "limit=101",
        "receipt=invalid",
    ],
)
def test_invalid_memory_filters_fail_as_client_errors(enterprise_client, query):
    client, _, _ = enterprise_client
    assert client.get(f"/api/lyte/v2/second-brain?{query}", headers=_headers()).status_code == 422


def test_api_real_ask_requires_authorized_scope_and_cites_real_projection(enterprise_client):
    client, runtime, scope = enterprise_client
    receipt = _receipt(runtime.store, scope)
    _row(
        runtime.store,
        scope,
        OperationalEntityKind.SERVICE,
        "checkout",
        {
            "error_budget_burn": 2.0,
        },
        (receipt.record_hash,),
    )
    question = {"question": "Which service has highest error budget burn?"}
    result = client.post("/api/lyte/v2/ask", headers=_headers(), json=question)
    assert result.status_code == 200 and result.json()["truth_label"] == "REPORTED"
    assert result.json()["confidence"] == 0.97
    assert client.post("/api/lyte/v2/ask", json=question).status_code == 401
    foreign_headers = _headers(**{"X-Lyte-Workspace-ID": "55555555-5555-4555-8555-555555555555"})
    assert (
        client.post("/api/lyte/v2/ask", headers=foreign_headers, json=question).status_code == 403
    )


def test_authorized_unprovisioned_workspace_is_a_bounded_scope_error(
    enterprise_client, monkeypatch
):
    client, runtime, _ = enterprise_client
    missing_id = "55555555-5555-4555-8555-555555555555"
    principal = runtime.authentication.authenticate_header(f"Bearer {DEV_TOKEN}")
    monkeypatch.setattr(
        runtime.authentication,
        "authenticate_header",
        lambda _: replace(principal, workspace_ids=frozenset({UUID(missing_id)})),
    )
    headers = _headers(**{"X-Lyte-Workspace-ID": missing_id})
    assert client.get("/api/lyte/v2/second-brain", headers=headers).status_code == 404
    assert (
        client.post(
            "/api/lyte/v2/ask",
            headers=headers,
            json={"question": "Which service has highest error budget burn?"},
        ).status_code
        == 404
    )
