"""Read-only full-stack receipt inspection; synthetic SQLite data, no signatures."""
from __future__ import annotations

import copy

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, update

from lyte.app import create_app
from lyte.domain import ReceiptDraft, TruthLabel, sha256_json
from lyte.persistence import PersistenceError
from lyte.persistence.models import ReceiptRecord


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("LYTE_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    monkeypatch.setenv("LYTE_DEMO_MODE", "true")
    monkeypatch.setenv("LYTE_DEV_AUTH_ENABLED", "false")
    for name in (
        "LYTE_SOURCE_REVISION", "SOURCE_REVISION", "GITHUB_SHA", "SPACE_ID",
        "LYTE_REQUIRE_SOURCE_BINDING", "LYTE_REQUIRE_BUILD_RECEIPT",
        "OIDC_ISSUER", "OIDC_AUDIENCE", "OIDC_JWKS_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    with TestClient(create_app()) as value:
        yield value


def append(client, refs=(), subject="fixture"):
    runtime = client.app.state.runtime
    return runtime.store.append_receipt(
        runtime.demo_scope,
        ReceiptDraft(
            kind="inspection.fixture", subject_type="test", subject_id=subject,
            payload={"value": 1}, truth_label=TruthLabel.SAMPLE, evidence_refs=tuple(refs),
        ),
    )


def inspect(client, digest):
    return client.get(f"/api/lyte/v2/receipts/{digest}/inspection")


def test_complete_local_references_never_imply_full_verification(client):
    child = append(client)
    root = append(client, [child.record_hash], "root")
    response = inspect(client, root.record_hash)
    assert response.status_code == 200
    body = response.json()
    assert body["record_integrity"] == "VERIFIED_LOCAL"
    assert body["status"] == "PARTIAL"
    assert body["data_mode"] == "SAMPLE"
    assert body["record_truth_label"] == "SAMPLE"
    assert body["expected_references"] == body["inspected_references"] == 1
    assert body["verified_references"] == 1
    assert body["coverage_state"] == "COMPLETE_LOCAL"
    assert body["references"][0]["record_hash"] == child.record_hash
    assert body["coverage_fraction"] == 1.0
    assert not any(body[k] for k in ("can_authorize", "can_execute", "effectors_enabled"))
    assert body["human_approval_required"] is True
    assert "SIGNATURE_NOT_VERIFIED" in body["limitations"]
    assert "EXTERNAL_FRESHNESS_NOT_ESTABLISHED" in body["limitations"]


def test_empty_evidence_is_not_100_percent_coverage(client):
    body = inspect(client, append(client).record_hash).json()
    assert body["expected_references"] == 0
    assert body["coverage_fraction"] is None
    assert body["coverage_state"] == "EMPTY_POPULATION"
    assert body["status"] == "PARTIAL"


def test_missing_and_nonreceipt_references_remain_unresolved(client):
    child = append(client)
    root = append(client, [child.record_hash, "f" * 64, "https://example.invalid/untrusted"])
    body = inspect(client, root.record_hash).json()
    assert body["expected_references"] == 3
    assert body["inspected_references"] == body["verified_references"] == 1
    assert body["coverage_state"] == "INCOMPLETE"
    assert body["coverage_fraction"] == pytest.approx(1 / 3)
    assert [r["state"] for r in body["references"]] == [
        "VERIFIED_LOCAL", "UNRESOLVED", "UNRESOLVED",
    ]
    assert "example.invalid" not in str(body)


def test_population_limit_does_not_silently_check_a_prefix(client):
    root = append(client, [f"{i:064x}" for i in range(33)])
    body = inspect(client, root.record_hash).json()
    assert body["expected_references"] == 33
    assert body["inspected_references"] == 0
    assert body["references"] == []
    assert body["coverage_fraction"] is None
    assert body["coverage_state"] == "LIMIT_EXCEEDED"


@pytest.mark.parametrize("field", ["payload_json", "payload_sha256", "kind", "truth_label"])
def test_corrupt_target_projection_fails_closed(client, field):
    root = append(client)
    value = {"value": 2} if field == "payload_json" else "c" * 64
    with client.app.state.runtime.database.session() as session, session.begin():
        session.execute(update(ReceiptRecord).where(
            ReceiptRecord.record_hash == root.record_hash,
        ).values(**{field: value}))
    response = inspect(client, root.record_hash)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == body["record_integrity"] == "FAILED"
    assert body["expected_references"] is None
    assert body["record_truth_label"] == "UNAVAILABLE"


def test_bool_integer_equivalence_cannot_hide_basis_corruption(client):
    root = append(client)
    basis = copy.deepcopy(root.basis_json)
    basis["data"]["payload"]["value"] = True
    assert basis == root.basis_json  # Python's equality is not a byte identity check.
    with client.app.state.runtime.database.session() as session, session.begin():
        session.execute(update(ReceiptRecord).where(
            ReceiptRecord.record_hash == root.record_hash,
        ).values(basis_json=basis))
    assert inspect(client, root.record_hash).json()["record_integrity"] == "FAILED"


def test_corrupted_direct_receipt_is_not_a_valid_dependency(client):
    child = append(client)
    root = append(client, [child.record_hash])
    with client.app.state.runtime.database.session() as session, session.begin():
        session.execute(update(ReceiptRecord).where(
            ReceiptRecord.record_hash == child.record_hash,
        ).values(payload_json={"value": 99}))
    body = inspect(client, root.record_hash).json()
    assert body["record_integrity"] == "VERIFIED_LOCAL"
    assert body["status"] == "FAILED"
    assert body["inspected_references"] == 1
    assert body["verified_references"] == 0
    assert body["references"][0]["state"] == "FAILED"


def test_duplicate_reference_never_inflates_the_population(client):
    child = append(client)
    root = append(client, [child.record_hash])
    # Model a malformed but internally rehashed producer record. Native ReceiptDraft
    # normally deduplicates refs; the inspector must not rely on that prior writer.
    basis = copy.deepcopy(root.basis_json)
    basis["data"]["evidence_refs"] = [child.record_hash, child.record_hash]
    changed_hash = sha256_json(basis)
    with client.app.state.runtime.database.session() as session, session.begin():
        session.execute(update(ReceiptRecord).where(
            ReceiptRecord.record_hash == root.record_hash,
        ).values(basis_json=basis, evidence_refs=basis["data"]["evidence_refs"],
                 record_hash=changed_hash))
    body = inspect(client, changed_hash).json()
    assert body["status"] == "FAILED"
    assert body["expected_references"] == 2
    assert body["verified_references"] == body["inspected_references"] == 1
    assert body["references"][1]["state"] == "DUPLICATE"


def test_reader_does_not_mutate_or_walk_the_entire_chain(client):
    child = append(client)
    root = append(client, [child.record_hash])
    runtime = client.app.state.runtime
    statements = []

    def record_statement(_connection, _cursor, statement, _params, _context, _many):
        statements.append(statement)

    event.listen(runtime.database.engine, "before_cursor_execute", record_statement)
    try:
        for _ in range(2):
            assert inspect(client, root.record_hash).status_code == 200
    finally:
        event.remove(runtime.database.engine, "before_cursor_execute", record_statement)
    assert all(
        not s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for s in statements
    )
    receipt_queries = [s for s in statements if "FROM lyte_receipts" in s]
    assert len(receipt_queries) == 4  # Target and one bounded direct-reference query per read.
    assert all("tenant_id" in s and "workspace_id" in s for s in receipt_queries)
    assert all("sequence ORDER" not in s for s in receipt_queries)


def test_tenant_headers_cannot_borrow_public_sample_scope(client):
    root = append(client)
    result = client.get(
        f"/api/lyte/v2/receipts/{root.record_hash}/inspection",
        headers={"X-Lyte-Tenant-ID": "33333333-3333-4333-8333-333333333333",
                 "X-Lyte-Workspace-ID": "44444444-4444-4444-8444-444444444444"},
    )
    assert result.status_code == 503
    assert result.json()["detail"] == "authentication is not configured; request denied"
    assert "references" not in result.json()


def test_unavailable_storage_returns_503_not_empty_success(client, monkeypatch):
    def unavailable(*_args):
        raise PersistenceError("synthetic database outage")
    monkeypatch.setattr(client.app.state.runtime.store, "inspect_receipt", unavailable)
    response = inspect(client, "a" * 64)
    assert response.status_code == 503
    assert "synthetic" not in response.text


@pytest.mark.parametrize("digest,expected", [("a" * 64, 404), ("A" * 64, 400), ("bad", 400)])
def test_unknown_or_invalid_identity(client, digest, expected):
    assert inspect(client, digest).status_code == expected


def test_openapi_contains_read_only_inspection_route(client):
    operations = client.get("/openapi.json").json()["paths"][
        "/api/lyte/v2/receipts/{record_hash}/inspection"
    ]
    assert set(operations) == {"get"}


def test_inspected_content_is_not_recursively_promoted(client):
    leaf = append(client, ["not-a-receipt"])
    middle = append(client, [leaf.record_hash])
    root = append(client, [middle.record_hash])
    result = inspect(client, root.record_hash).json()
    assert result["verified_references"] == 1
    assert result["status"] == "PARTIAL"
    assert result["dependency_scope"] == "DIRECT_SCOPED_RECEIPTS_ONLY"
    assert "FULL_CHAIN_NOT_CHECKED" in result["limitations"]
    # Even a perfect immediate count explicitly cannot authorize an operation.
    assert result["can_authorize"] is False


def test_authenticated_principal_cannot_inspect_another_workspace(client, monkeypatch):
    scope = client.app.state.runtime.demo_scope
    token = "inspection-fixture-token-" + "x" * 40
    monkeypatch.setenv("LYTE_DEV_AUTH_ENABLED", "true")
    monkeypatch.setenv("LYTE_DEV_AUTH_TOKEN", token)
    monkeypatch.setenv("LYTE_DEV_TENANT_ID", str(scope.tenant_id))
    monkeypatch.setenv("LYTE_DEV_WORKSPACE_ID", str(scope.workspace_id))
    with TestClient(create_app()) as authenticated:
        root = append(authenticated)
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Lyte-Tenant-ID": str(scope.tenant_id),
            "X-Lyte-Workspace-ID": str(scope.workspace_id),
        }
        url = f"/api/lyte/v2/receipts/{root.record_hash}/inspection"
        allowed = authenticated.get(url, headers=headers)
        assert allowed.status_code == 200
        headers["X-Lyte-Workspace-ID"] = "44444444-4444-4444-8444-444444444444"
        denied = authenticated.get(url, headers=headers)
        assert denied.status_code == 403
        assert "references" not in denied.json()
        assert denied.json()["detail"] == "the verified principal is not authorized for this scope"
