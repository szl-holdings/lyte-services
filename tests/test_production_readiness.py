from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select, text, update
from sqlalchemy.exc import IntegrityError, OperationalError

from lyte.api.routes_admin import router as admin_router
from lyte.api.routes_health import router as health_router
from lyte.app import _make_runtime, create_app
from lyte.domain import ReceiptDraft, Scope, TenantSpec, TruthLabel, WorkspaceSpec, sha256_json
from lyte.governance import AuthenticationError, AuthenticationService, Principal
from lyte.persistence import (
    EXPECTED_SCHEMA_REVISIONS,
    STREAM_ANATOMY,
    STREAM_IDEMPOTENCY,
    STREAM_MEMORY,
    STREAM_OPERATIONAL,
    STREAM_RECEIPTS,
    Database,
    LyteStore,
    OperationalRecord,
    ProvisioningConflict,
    ReceiptRecord,
    ScopeProvisioning,
    StreamHeadRecord,
    TenantRecord,
    WorkspaceRecord,
)
from lyte.settings import ConfigurationError

ROOT = Path(__file__).parents[1]
TENANT_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
WORKSPACE_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
OTHER_WORKSPACE_ID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
ADMIN_CREDENTIAL = "fixture-admin-bearer"
OPERATOR_CREDENTIAL = "fixture-operator-bearer"
WRONG_SCOPE_CREDENTIAL = "fixture-wrong-scope-bearer"


class _FixtureVerifier:
    def __init__(self, principals: Mapping[str, Principal]) -> None:
        self.principals = principals

    def verify(self, token: str) -> Principal:
        try:
            return self.principals[token]
        except KeyError as exc:
            raise AuthenticationError("fixture credential rejected") from exc


class _Metrics:
    def __init__(self) -> None:
        self.database_healthy: bool | None = None

    def set_db_pool_healthy(self, value: bool) -> None:
        self.database_healthy = value


def _principal(*, roles: frozenset[str], workspace_id: UUID = WORKSPACE_ID) -> Principal:
    return Principal(
        subject="production-admin",
        tenant_id=TENANT_ID,
        workspace_ids=frozenset({workspace_id}),
        roles=roles,
        issuer="https://issuer.example",
    )


def _migrated_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Database:
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'production.db').as_posix()}"
    monkeypatch.setenv("LYTE_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", database_url)
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    command.upgrade(config, "head")
    return Database(database_url)


def _runtime(database: Database, *, demo_mode: bool = False) -> SimpleNamespace:
    verifier = _FixtureVerifier(
        {
            ADMIN_CREDENTIAL: _principal(roles=frozenset({"admin"})),
            OPERATOR_CREDENTIAL: _principal(roles=frozenset({"operator"})),
            WRONG_SCOPE_CREDENTIAL: _principal(
                roles=frozenset({"admin"}), workspace_id=OTHER_WORKSPACE_ID
            ),
        }
    )
    return SimpleNamespace(
        settings=SimpleNamespace(is_production=True),
        database=database,
        store=LyteStore(database),
        authentication=AuthenticationService(verifier),
        demo_mode=demo_mode,
        metrics=_Metrics(),
        require_source_binding=False,
    )


def _app_with_runtime(runtime: Any, *, health: bool = False) -> FastAPI:
    application = FastAPI()
    application.include_router(health_router if health else admin_router)
    application.state.runtime = runtime
    return application


def _payload(**overrides: str) -> dict[str, str]:
    payload = {
        "tenant_id": str(TENANT_ID),
        "tenant_slug": "northstar-enterprise",
        "tenant_name": "Northstar Enterprise",
        "workspace_id": str(WORKSPACE_ID),
        "workspace_slug": "production-command",
        "workspace_name": "Production Command",
    }
    payload.update(overrides)
    return payload


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_declared_schema_revision_matches_alembic_head() -> None:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    scripts = ScriptDirectory.from_config(config)
    assert tuple(sorted(scripts.get_heads())) == EXPECTED_SCHEMA_REVISIONS


def test_fresh_production_scope_provisioning_is_admin_bounded_and_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _migrated_database(tmp_path, monkeypatch)
    runtime = _runtime(database)
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(TenantRecord)) == 0
        assert session.scalar(select(func.count()).select_from(OperationalRecord)) == 0

    with TestClient(_app_with_runtime(runtime)) as client:
        first = client.post(
            "/api/lyte/v2/admin/scopes",
            json=_payload(),
            headers=_headers(ADMIN_CREDENTIAL),
        )
        replay = client.post(
            "/api/lyte/v2/admin/scopes",
            json=_payload(),
            headers=_headers(ADMIN_CREDENTIAL),
        )

    assert first.status_code == 201
    receipt_hash = first.json()["receipt_hash"]
    assert len(receipt_hash) == 64
    assert first.json() == {
        "schema": "szl.lyte.scope-provisioning/v1",
        "state": "CREATED",
        "scope": {"tenant_id": str(TENANT_ID), "workspace_id": str(WORKSPACE_ID)},
        "tenant_created": True,
        "workspace_created": True,
        "stream_kinds_initialized": True,
        "receipt_hash": receipt_hash,
        "receipt_signature_state": "UNAVAILABLE",
        "sample_data_seeded": False,
        "data_mode": "REAL",
        "effectors_enabled": False,
        "human_approval_required": True,
        "truth_label": "MEASURED",
    }
    assert replay.status_code == 200
    assert replay.json()["state"] == "EXISTING"
    assert replay.json()["tenant_created"] is False
    assert replay.json()["workspace_created"] is False
    assert replay.json()["receipt_hash"] == receipt_hash
    assert runtime.store.verify_chain(Scope(TENANT_ID, WORKSPACE_ID), STREAM_RECEIPTS).valid

    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(TenantRecord)) == 1
        assert session.scalar(select(func.count()).select_from(WorkspaceRecord)) == 1
        assert session.scalar(select(func.count()).select_from(OperationalRecord)) == 0
        assert session.scalar(select(func.count()).select_from(ReceiptRecord)) == 1
        assert frozenset(session.scalars(select(StreamHeadRecord.stream_kind))) == {
            STREAM_RECEIPTS,
            STREAM_IDEMPOTENCY,
            STREAM_MEMORY,
            STREAM_ANATOMY,
            STREAM_OPERATIONAL,
        }
    database.dispose()


def test_provisioning_receipt_failure_rolls_back_scope_and_retry_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _migrated_database(tmp_path, monkeypatch)
    runtime = _runtime(database)

    def fail_receipt(_conn: object, _cursor: object, statement: str, *_args: object) -> None:
        if statement.lstrip().upper().startswith("INSERT INTO LYTE_RECEIPTS"):
            raise OperationalError("test receipt insert", {}, RuntimeError("injected failure"))

    event.listen(database.engine, "before_cursor_execute", fail_receipt)
    try:
        with TestClient(_app_with_runtime(runtime)) as client:
            failed = client.post(
                "/api/lyte/v2/admin/scopes", json=_payload(), headers=_headers(ADMIN_CREDENTIAL)
            )
        assert failed.status_code == 503
        assert "injected" not in failed.text
        with database.session() as session:
            for model in (TenantRecord, WorkspaceRecord, StreamHeadRecord, ReceiptRecord):
                assert session.scalar(select(func.count()).select_from(model)) == 0
    finally:
        event.remove(database.engine, "before_cursor_execute", fail_receipt)
    with TestClient(_app_with_runtime(runtime)) as client:
        retry = client.post(
            "/api/lyte/v2/admin/scopes", json=_payload(), headers=_headers(ADMIN_CREDENTIAL)
        )
    assert retry.status_code == 201
    assert len(retry.json()["receipt_hash"]) == 64
    database.dispose()


@pytest.mark.parametrize(
    "corruption",
    ("kind", "evidence_refs", "created_at", "basis_shape", "data_shape", "identity", "duplicate"),
)
def test_provisioning_replay_rejects_invalid_receipt_without_mutating_streams(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, corruption: str,
) -> None:
    database = _migrated_database(tmp_path, monkeypatch)
    runtime = _runtime(database)
    scope = Scope(TENANT_ID, WORKSPACE_ID)
    with TestClient(_app_with_runtime(runtime)) as client:
        first = client.post(
            "/api/lyte/v2/admin/scopes", json=_payload(), headers=_headers(ADMIN_CREDENTIAL)
        )
        assert first.status_code == 201, first.text
        receipt_hash = first.json()["receipt_hash"]
        with database.session() as session:
            receipt = session.scalar(
                select(ReceiptRecord).where(ReceiptRecord.record_hash == receipt_hash)
            )
            payload = deepcopy(receipt.payload_json)
            basis = deepcopy(receipt.basis_json)
        if corruption == "duplicate":
            runtime.store.append_receipt(
                scope,
                ReceiptDraft(
                    kind="scope.provisioned",
                    subject_type="workspace",
                    subject_id=str(WORKSPACE_ID),
                    payload=payload,
                    truth_label=TruthLabel.MEASURED,
                ),
            )
        else:
            replacement_hash = None
            if corruption == "kind":
                changed_fields = {"kind": "altered.kind"}
            elif corruption == "evidence_refs":
                changed_fields = {"evidence_refs": ["altered:evidence"]}
            elif corruption == "created_at":
                changed_fields = {"created_at": datetime(2000, 1, 1, tzinfo=UTC)}
            elif corruption == "basis_shape":
                changed_fields = {"basis_json": ["invalid canonical basis shape"]}
            elif corruption == "data_shape":
                basis["data"] = ["invalid canonical data shape"]
                changed_fields = {"basis_json": basis}
            else:
                # A self-consistent row still must identify the actual scope,
                # rather than passing merely because its hash was recomputed.
                payload["tenant"]["name"] = "Different tenant identity"
                basis["data"]["payload"] = payload
                basis["data"]["payload_sha256"] = sha256_json(payload)
                replacement_hash = sha256_json(basis)
                changed_fields = {
                    "payload_json": payload,
                    "payload_sha256": sha256_json(payload),
                    "basis_json": basis,
                    "record_hash": replacement_hash,
                }
            with database.session() as session, session.begin():
                # Simulate a damaged privileged restore in this disposable
                # database. Restore the migration's guard before API replay;
                # ordinary application SQL cannot mutate these records.
                guard_sql = session.execute(
                    text(
                        "SELECT sql FROM sqlite_master WHERE type = 'trigger' "
                        "AND name = 'trg_lyte_receipts_immutable_update'"
                    )
                ).scalar_one()
                session.execute(text("DROP TRIGGER trg_lyte_receipts_immutable_update"))
                session.execute(
                    update(ReceiptRecord)
                    .where(ReceiptRecord.record_hash == receipt_hash)
                    .values(**changed_fields)
                )
                if replacement_hash is not None:
                    session.execute(
                        update(StreamHeadRecord)
                        .where(
                            StreamHeadRecord.tenant_id == str(TENANT_ID),
                            StreamHeadRecord.workspace_id == str(WORKSPACE_ID),
                            StreamHeadRecord.stream_kind == STREAM_RECEIPTS,
                        )
                        .values(last_hash=replacement_hash)
                    )
                session.execute(text(guard_sql))

        def stream_snapshot():
            with database.session() as session:
                return list(
                    session.execute(
                        select(
                            StreamHeadRecord.stream_kind,
                            StreamHeadRecord.last_sequence,
                            StreamHeadRecord.last_hash,
                        ).order_by(StreamHeadRecord.stream_kind)
                    )
                )

        before = stream_snapshot()
        replay = client.post(
            "/api/lyte/v2/admin/scopes", json=_payload(), headers=_headers(ADMIN_CREDENTIAL)
        )
        assert replay.status_code == 409, replay.text
        assert replay.json()["detail"] == (
            "scope identity conflicts with an existing provisioned resource"
        )
        assert stream_snapshot() == before
    database.dispose()


def test_provisioning_denies_anonymous_non_admin_wrong_scope_demo_and_conflicts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _migrated_database(tmp_path, monkeypatch)
    runtime = _runtime(database)
    with TestClient(_app_with_runtime(runtime)) as client:
        anonymous = client.post("/api/lyte/v2/admin/scopes", json=_payload())
        operator = client.post(
            "/api/lyte/v2/admin/scopes",
            json=_payload(),
            headers=_headers(OPERATOR_CREDENTIAL),
        )
        wrong_scope = client.post(
            "/api/lyte/v2/admin/scopes",
            json=_payload(),
            headers=_headers(WRONG_SCOPE_CREDENTIAL),
        )
        created = client.post(
            "/api/lyte/v2/admin/scopes",
            json=_payload(),
            headers=_headers(ADMIN_CREDENTIAL),
        )
        conflict = client.post(
            "/api/lyte/v2/admin/scopes",
            json=_payload(workspace_name="Conflicting Name"),
            headers=_headers(ADMIN_CREDENTIAL),
        )
    assert anonymous.status_code == 401
    assert anonymous.headers["www-authenticate"] == "Bearer"
    assert operator.status_code == 403
    assert wrong_scope.status_code == 403
    assert created.status_code == 201
    assert conflict.status_code == 409

    demo_runtime = _runtime(database, demo_mode=True)
    with TestClient(_app_with_runtime(demo_runtime)) as client:
        demo = client.post(
            "/api/lyte/v2/admin/scopes",
            json=_payload(),
            headers=_headers(ADMIN_CREDENTIAL),
        )
    assert demo.status_code == 409
    assert "SAMPLE" in demo.json()["detail"]
    database.dispose()


@pytest.mark.parametrize(
    ("schema_kind", "expected_state"),
    [
        ("empty", "MISSING_REQUIRED_TABLES"),
        ("unversioned", "MISSING_MIGRATION_REVISION"),
        ("stale", "STALE_MIGRATION_REVISION"),
    ],
)
def test_production_readiness_fails_for_missing_or_stale_schema(
    schema_kind: str,
    expected_state: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if schema_kind == "stale":
        database = _migrated_database(tmp_path, monkeypatch)
        with database.engine.begin() as connection:
            connection.execute(
                text("UPDATE alembic_version SET version_num = 'obsolete_revision'")
            )
    else:
        database = Database(f"sqlite+pysqlite:///{(tmp_path / 'schema.db').as_posix()}")
        if schema_kind == "unversioned":
            database.create_schema()

    runtime = _runtime(database)
    with TestClient(_app_with_runtime(runtime, health=True)) as client:
        response = client.get("/readyz")

    assert response.status_code == 503
    body = response.json()
    assert body["ready"] is False
    assert body["checks"]["database"] == "READY"
    assert body["checks"]["schema"] == expected_state
    assert body["database_schema"]["truth_label"] == "UNAVAILABLE"
    assert runtime.metrics.database_healthy is True
    database.dispose()


def test_production_readiness_requires_and_reports_exact_migrated_head(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _migrated_database(tmp_path, monkeypatch)
    runtime = _runtime(database)
    with TestClient(_app_with_runtime(runtime, health=True)) as client:
        response = client.get("/readyz")

    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is True
    assert body["checks"]["schema"] == "READY_MIGRATED"
    assert body["database_schema"]["expected_revisions"] == ["20260904_0001"]
    assert body["database_schema"]["observed_revisions"] == ["20260904_0001"]
    assert body["database_schema"]["truth_label"] == "MEASURED"
    database.dispose()


def test_empty_alembic_table_is_damaged_metadata_even_in_local_mode(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{(tmp_path / 'empty-head.db').as_posix()}")
    database.create_schema()
    with database.engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
    observed = database.schema_readiness(require_migration_revision=False)
    assert observed.ready is False
    assert observed.state == "MISSING_MIGRATION_REVISION"
    assert observed.observed_revisions == ()
    database.dispose()


def test_provisioning_retries_only_the_benign_shared_tenant_race(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = Database("sqlite+pysqlite:///:memory:")
    database.create_schema()
    store = LyteStore(database)
    tenant = TenantSpec("tenant-a", "Tenant A", TENANT_ID)
    workspace = WorkspaceSpec(TENANT_ID, "workspace-a", "Workspace A", WORKSPACE_ID)
    expected = ScopeProvisioning(
        scope=Scope(TENANT_ID, WORKSPACE_ID),
        state="CREATED",
        tenant_created=False,
        workspace_created=True,
    )
    attempts = 0

    def provision_once(*_args: object) -> ScopeProvisioning:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise IntegrityError("INSERT tenant", {}, RuntimeError("unique race"))
        return expected

    def missing_workspace(*_args: object) -> ScopeProvisioning:
        raise ProvisioningConflict("scope provisioning did not converge")

    monkeypatch.setattr(store, "_provision_scope_once", provision_once)
    monkeypatch.setattr(store, "_read_provisioned_scope", missing_workspace)
    monkeypatch.setattr(store, "_retryable_missing_workspace", lambda *_args: True)
    assert store.provision_scope(tenant, workspace) == expected
    assert attempts == 2
    database.dispose()


def test_production_startup_never_implicitly_enables_or_seeds_demo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = {
        "LYTE_ENV": "production",
        "DATABASE_URL": "postgresql+psycopg://lyte@127.0.0.1:9/lyte",
        "OIDC_ISSUER": "https://issuer.example",
        "OIDC_AUDIENCE": "lyte-production",
        "OIDC_JWKS_URL": "https://issuer.example/.well-known/jwks.json",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("LYTE_DEMO_MODE", raising=False)
    monkeypatch.setattr(
        "lyte.app.seed_demo",
        lambda *_args, **_kwargs: pytest.fail("production attempted to seed SAMPLE data"),
    )
    application = create_app()
    runtime = _make_runtime(application)
    assert runtime.demo_mode is False
    assert runtime.demo_seed is None
    runtime.database.dispose()

    monkeypatch.setenv("LYTE_DEMO_MODE", "true")
    with pytest.raises(ConfigurationError, match="forbidden in production"):
        _make_runtime(application)
