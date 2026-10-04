"""PostgreSQL-only migration, tenancy, and concurrent provisioning proof."""

from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import event, func, select

from lyte.domain import ReceiptDraft, Scope, TenantSpec, TruthLabel, WorkspaceSpec, sha256_json
from lyte.governance import MemoryDraft, MemoryKind
from lyte.intelligence.living_anatomy import (
    ANALYSIS_ANATOMY_SCHEMA,
    build_analysis_anatomy,
)
from lyte.persistence import (
    STREAM_ANATOMY,
    STREAM_IDEMPOTENCY,
    STREAM_MEMORY,
    STREAM_OPERATIONAL,
    STREAM_RECEIPTS,
    Database,
    IdempotencyConflict,
    LyteStore,
    ProvisioningConflict,
    StreamHeadRecord,
    WorkspaceRecord,
)


def _database() -> Database:
    url = os.getenv("LYTE_TEST_POSTGRES_URL", "").strip()
    if not url:
        pytest.skip("LYTE_TEST_POSTGRES_URL is required for PostgreSQL contract proof")
    database = Database(url)
    if database.engine.dialect.name != "postgresql":
        database.dispose()
        pytest.fail("LYTE_TEST_POSTGRES_URL must use PostgreSQL")
    return database


def test_postgres_exact_migration_and_concurrent_shared_tenant_bootstrap() -> None:
    database = _database()
    schema = database.schema_readiness(require_migration_revision=True)
    assert schema.ready is True
    assert schema.state == "READY_MIGRATED"
    database.ping()

    tenant_id = uuid4()
    first_workspace_id = uuid4()
    second_workspace_id = uuid4()
    suffix = tenant_id.hex[:12]
    tenant = TenantSpec(f"tenant-{suffix}", "Concurrent Tenant", tenant_id)
    workspaces = (
        WorkspaceSpec(
            tenant_id,
            f"workspace-a-{suffix}",
            "Workspace A",
            first_workspace_id,
        ),
        WorkspaceSpec(
            tenant_id,
            f"workspace-b-{suffix}",
            "Workspace B",
            second_workspace_id,
        ),
    )
    barrier = threading.Barrier(2)
    lock = threading.Lock()
    synchronized_queries = 0

    def synchronize_missing_tenant(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        nonlocal synchronized_queries
        normalized = " ".join(statement.lower().split())
        if " from lyte_tenants " not in f" {normalized} " or " for update" not in normalized:
            return
        with lock:
            if synchronized_queries >= 2:
                return
            synchronized_queries += 1
        barrier.wait(timeout=15)

    event.listen(database.engine, "after_cursor_execute", synchronize_missing_tenant)
    store = LyteStore(database)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(store.provision_scope, tenant, item) for item in workspaces]
            results = [future.result(timeout=30) for future in futures]
    finally:
        event.remove(database.engine, "after_cursor_execute", synchronize_missing_tenant)

    assert synchronized_queries == 2
    assert {result.scope.workspace_id for result in results} == {
        first_workspace_id,
        second_workspace_id,
    }
    assert all(result.state == "CREATED" for result in results)
    expected_streams = {
        STREAM_ANATOMY,
        STREAM_IDEMPOTENCY,
        STREAM_MEMORY,
        STREAM_OPERATIONAL,
        STREAM_RECEIPTS,
    }
    with database.session() as session:
        persisted_workspaces = session.scalar(
            select(func.count()).select_from(WorkspaceRecord).where(
                WorkspaceRecord.tenant_id == str(tenant_id)
            )
        )
        persisted_streams = session.scalar(
            select(func.count()).select_from(StreamHeadRecord).where(
                StreamHeadRecord.tenant_id == str(tenant_id)
            )
        )
    assert persisted_workspaces == 2
    assert persisted_streams == 2 * len(expected_streams)

    conflicting = WorkspaceSpec(
        tenant_id,
        workspaces[0].slug,
        "Conflicting Workspace",
        uuid4(),
    )
    with pytest.raises(ProvisioningConflict):
        store.provision_scope(tenant, conflicting)
    database.dispose()


def _analysis_arguments(scope: Scope) -> tuple[ReceiptDraft, MemoryDraft, tuple, str]:
    input_sha = sha256_json({"sample_aggregate": 99})
    output_sha = sha256_json({"sample_formula": 0.99})
    anatomy = build_analysis_anatomy(
        input_sha256=input_sha,
        truth_label=TruthLabel.SAMPLE,
        source_revision="a" * 40,
        stage_results=tuple(
            (state, {"sample_stage": index})
            for index, state in enumerate(
                (
                    "OBSERVED", "VALIDATED", "BOUND", "CALCULATED", "ENFORCED",
                    "REVIEW", "PARTIAL", "PERSISTED", "APPENDED",
                ),
                start=1,
            )
        ),
    )
    anatomy_sha = sha256_json(
        [
            {
                "sequence": stage.spec.sequence,
                "stage": stage.spec.stage,
                "state": stage.state,
                "input_sha256": stage.input_sha256,
                "basis_sha256": stage.basis_sha256,
                "output_sha256": stage.output_sha256,
            }
            for stage in anatomy.stages
        ]
    )
    receipt = ReceiptDraft(
        kind="analysis.completed",
        subject_type="service",
        subject_id="sample-service",
        truth_label=TruthLabel.SAMPLE,
        evidence_refs=("sample:postgres-concurrency",),
        payload={
            "trace_id": str(anatomy.trace_id),
            "anatomy_schema": ANALYSIS_ANATOMY_SCHEMA,
            "anatomy_sha256": anatomy_sha,
            "input_sha256": input_sha,
            "output_sha256": output_sha,
            "formula_output_sha256": output_sha,
            "source_revision": "a" * 40,
            "human_approval_required": True,
            "can_authorize": False,
            "can_execute": False,
            "effectors_enabled": False,
        },
    )
    memory = MemoryDraft(
        kind=MemoryKind.OBSERVATION,
        summary="SAMPLE analysis concurrency fixture; no effectors.",
        truth_label=TruthLabel.SAMPLE,
        evidence_refs=("sample:postgres-concurrency",),
        subjects=("sample-service",),
        metadata={
            "trace_id": str(anatomy.trace_id),
            "analysis_input_sha256": input_sha,
            "analysis_output_sha256": output_sha,
            "approved_knowledge": False,
        },
    )
    return receipt, memory, anatomy.to_traces(scope), sha256_json({"request": input_sha})


@pytest.mark.parametrize("different_request", (False, True))
def test_postgres_concurrent_analysis_key_commits_one_complete_bundle(different_request) -> None:
    database = _database()
    tenant = TenantSpec(f"anatomy-{uuid4().hex[:12]}", "Anatomy concurrency")
    workspace = WorkspaceSpec(tenant.id, "concurrent-analysis", "Concurrent analysis")
    store = LyteStore(database)
    scope = store.provision_scope(tenant, workspace).scope
    streams = (STREAM_RECEIPTS, STREAM_IDEMPOTENCY, STREAM_MEMORY, STREAM_ANATOMY)
    before = {stream: store.verify_chain(scope, stream).record_count for stream in streams}
    barrier = threading.Barrier(2)
    lock = threading.Lock()
    synchronized_queries = 0

    def synchronize_missing_key(
        _connection, _cursor, statement, _parameters, _context, _executemany
    ):
        nonlocal synchronized_queries
        normalized = " ".join(statement.lower().split())
        if " from lyte_idempotency_records " not in f" {normalized} ":
            return
        with lock:
            if synchronized_queries >= 2:
                return
            synchronized_queries += 1
        barrier.wait(timeout=15)

    arguments = [_analysis_arguments(scope), _analysis_arguments(scope)]
    if different_request:
        receipt, memory, traces, _digest = arguments[1]
        arguments[1] = (receipt, memory, traces, sha256_json({"different_request": True}))

    def append(arguments):
        receipt, memory, traces, digest = arguments
        try:
            return store.append_analysis_bundle(
                scope,
                receipt,
                memory,
                traces,
                idempotency_key="postgres-anatomy-concurrent-key-0001",
                request_digest=digest,
            )
        except IdempotencyConflict:
            return "CONFLICT"

    event.listen(database.engine, "after_cursor_execute", synchronize_missing_key)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(append, argument) for argument in arguments]
            results = [future.result(timeout=30) for future in futures]
    finally:
        event.remove(database.engine, "after_cursor_execute", synchronize_missing_key)
    assert synchronized_queries == 2
    bundles = [result for result in results if result != "CONFLICT"]
    if different_request:
        assert len(bundles) == 1
        assert results.count("CONFLICT") == 1
        assert bundles[0].idempotent_replay is False
    else:
        assert len(bundles) == 2
        assert {bundle.idempotent_replay for bundle in bundles} == {False, True}
        assert len({bundle.receipt.record_hash for bundle in bundles}) == 1
        assert len({bundle.memory.record_hash for bundle in bundles}) == 1
        assert len({tuple(trace.record_hash for trace in bundle.traces) for bundle in bundles}) == 1
    assert {stream: store.verify_chain(scope, stream).record_count for stream in streams} == {
        STREAM_RECEIPTS: before[STREAM_RECEIPTS] + 1,
        STREAM_IDEMPOTENCY: before[STREAM_IDEMPOTENCY] + 1,
        STREAM_MEMORY: before[STREAM_MEMORY] + 1,
        STREAM_ANATOMY: before[STREAM_ANATOMY] + 9,
    }
    assert all(store.verify_chain(scope, stream).valid for stream in streams)
    database.dispose()
