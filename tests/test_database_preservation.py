"""Preservation evidence uses real SQLite WAL and, when configured, PostgreSQL."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

from lyte.domain import (
    AnatomyOrgan,
    AnatomyTrace,
    OperationalEntityKind,
    OperationalRecordDraft,
    ReceiptDraft,
    Scope,
    TenantSpec,
    TraceState,
    TruthLabel,
    WorkspaceSpec,
)
from lyte.governance import MemoryDraft, MemoryKind
from lyte.persistence import Database, LyteStore
from lyte.persistence import preservation as recovery
from lyte.persistence.models import Base
from lyte.persistence.store import STREAM_KINDS


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "live.sqlite"
    database = Database(f"sqlite+pysqlite:///{path.as_posix()}")
    database.create_schema()
    keeper = sqlite3.connect(path)
    keeper.execute("PRAGMA journal_mode=WAL")
    keeper.execute("PRAGMA wal_autocheckpoint=0")
    store = LyteStore(database)
    scopes = []
    for index in range(2):
        tenant = TenantSpec(f"tenant-{index}", "Synthetic Test Tenant")
        workspace = WorkspaceSpec(tenant.id, f"workspace-{index}", "Synthetic Workspace")
        store.create_tenant(tenant)
        store.create_workspace(workspace)
        scope = Scope(tenant.id, workspace.id)
        scopes.append(scope)
        for sequence in range(2):
            receipt = store.append_receipt(
                scope,
                ReceiptDraft(
                    kind="sample.observed",
                    subject_type="service",
                    subject_id="s",
                    payload={"sensitive_test_value": "must-not-enter-receipts", "n": sequence},
                    truth_label=TruthLabel.SAMPLE,
                ),
                idempotency_key=f"sample-request-key-{sequence}",
            )
        store.append_memory(
            scope,
            MemoryDraft(
                kind=MemoryKind.OBSERVATION,
                summary="Synthetic observation",
                truth_label=TruthLabel.SAMPLE,
                evidence_refs=(receipt.record_hash,),
                subjects=("s",),
                subject_dimensions={"service": ("s",)},
            ),
            receipt_hash=receipt.record_hash,
        )
        at = datetime(2026, 10, 21, 10, tzinfo=UTC)
        trace = AnatomyTrace.start(
            scope, organ=AnatomyOrgan.TRUST_GATE, operation="sample", input_sha256="1" * 64, now=at
        ).finish(
            state=TraceState.COMPLETED, output_sha256="2" * 64, now=at + timedelta(milliseconds=4)
        )
        store.append_anatomy_trace(trace, created_at=at)
        store.insert_operational(
            scope,
            OperationalRecordDraft(
                entity_kind=OperationalEntityKind.SERVICE,
                entity_id="service-1",
                name="Synthetic Service",
                body={"sample": True},
                truth_label=TruthLabel.SAMPLE,
            ),
        )
    yield path, database, scopes
    keeper.close()
    database.dispose()


def test_online_backup_captures_wal_and_every_scope_without_source_changes(source, tmp_path):
    path, database, scopes = source
    assert Path(str(path) + "-wal").stat().st_size > 0
    with database.engine.connect() as connection:
        before = recovery.summarize(connection)
    output = tmp_path / "backup"
    result = recovery.backup(path, output)
    assert result["summary"] == before
    assert result["summary"]["verified_streams"] == len(scopes) * 5
    assert result["snapshot_sha256"] == recovery.file_hash(output / "snapshot.sqlite")
    recovered = Database(f"sqlite+pysqlite:///{(output / 'snapshot.sqlite').as_posix()}")
    try:
        store = LyteStore(recovered)
        for scope in scopes:
            assert len(store.list_receipts(scope)) == 2
            assert all(store.verify_chain(scope, stream).valid for stream in STREAM_KINDS)
    finally:
        recovered.dispose()
    with database.engine.connect() as connection:
        assert recovery.summarize(connection) == before
    receipt = json.loads((output / "result.json").read_bytes())
    assert receipt["signed"] is False
    assert receipt["cutover_authorized"] is False
    assert receipt["previous_receipt_sha256"] == recovery.file_hash(output / "intent.json")
    assert "must-not-enter-receipts" not in (output / "result.json").read_text()


def test_backup_refuses_overwrite_and_missing_source(source, tmp_path):
    path, _, _ = source
    output = tmp_path / "existing"
    output.mkdir()
    with pytest.raises(FileExistsError):
        recovery.backup(path, output)
    assert not list(output.iterdir())
    with pytest.raises(recovery.PreservationError, match="SOURCE_FILE_REQUIRED"):
        recovery.backup(tmp_path / "missing", tmp_path / "never-created")
    assert not (tmp_path / "never-created").exists()


@pytest.mark.parametrize(
    "sql,code",
    [
        (
            "UPDATE lyte_receipts SET record_hash = '" + "0" * 64 + "' WHERE sequence=1",
            "HASH_CHAIN_MISMATCH",
        ),
        ("UPDATE lyte_stream_heads SET last_sequence = 99", "STREAM_HEAD_MISMATCH"),
        ("DELETE FROM lyte_stream_heads WHERE stream_kind = 'memory'", "STREAM_HEAD_SET_MISMATCH"),
        ("UPDATE lyte_receipts SET workspace_id = 'missing'", "FOREIGN_KEY_CHECK_FAILED"),
        ("CREATE TABLE unrecognized_history (value TEXT)", "SCHEMA_TABLE_SET_MISMATCH"),
        ("CREATE VIEW unrecognized_view AS SELECT * FROM lyte_tenants", "SCHEMA_VIEWS_UNSUPPORTED"),
        ("UPDATE lyte_receipts SET payload_json = '{}'", "PAYLOAD_DIGEST_MISMATCH"),
        ("UPDATE lyte_receipts SET subject_id = 'changed'", "HASH_CHAIN_MISMATCH"),
        ("UPDATE lyte_idempotency_records SET request_digest = 'changed'", "HASH_CHAIN_MISMATCH"),
        ("UPDATE lyte_memory_records SET metadata_json = '{}'", "HASH_CHAIN_MISMATCH"),
        ("UPDATE lyte_anatomy_traces SET operation = 'changed'", "HASH_CHAIN_MISMATCH"),
        ("UPDATE lyte_operational_records SET body_json = '{}'", "HASH_CHAIN_MISMATCH"),
        ("UPDATE lyte_receipts SET created_at = '2020-01-01 00:00:00'", "HASH_CHAIN_MISMATCH"),
    ],
)
def test_incomplete_or_tampered_backup_is_retained_but_not_qualified(source, tmp_path, sql, code):
    path, _, _ = source
    with sqlite3.connect(path) as connection:
        connection.execute(sql)
    output = tmp_path / "failed"
    with pytest.raises(recovery.PreservationError, match=code):
        recovery.backup(path, output)
    receipt = json.loads((output / "failure.json").read_bytes())
    assert receipt["effect"] == "SNAPSHOT_NOT_QUALIFIED"
    assert receipt["truth_label"] == "BLOCKED"
    assert (output / "snapshot.sqlite").is_file()
    assert not (output / "result.json").exists()


def test_restore_rejects_mutated_input_before_target_connection(source, tmp_path, monkeypatch):
    path, _, _ = source
    result = recovery.backup(path, tmp_path / "backup")
    snapshot = tmp_path / "backup/snapshot.sqlite"
    with snapshot.open("ab") as stream:
        stream.write(b"changed")
    monkeypatch.setattr(recovery, "_target", lambda: pytest.fail("must not contact target"))
    with pytest.raises(recovery.PreservationError, match="SNAPSHOT_DIGEST_MISMATCH"):
        recovery.restore(snapshot, result["snapshot_sha256"], "a" * 64, tmp_path / "restore")
    assert json.loads((tmp_path / "restore/failure.json").read_bytes())["effect"] == "NOT_COMMITTED"


def test_backup_timeout_and_cli_errors_are_honest_and_do_not_print_credentials(
    source, tmp_path, monkeypatch, capsys
):
    path, _, _ = source
    with pytest.raises(recovery.PreservationError, match="BACKUP_TIMEOUT"):
        recovery.backup(path, tmp_path / "timeout", timeout_seconds=-1)
    monkeypatch.setattr("sys.argv", ["preservation", "inspect-target"])
    monkeypatch.setattr(
        recovery,
        "_target",
        lambda: (_ for _ in ()).throw(
            RuntimeError("postgresql://sensitive:password@private-db/data")
        ),
    )
    assert recovery.main() == 1
    output = capsys.readouterr().out
    assert "password" not in output
    assert "private-db" not in output
    assert json.loads(output)["truth_label"] == "BLOCKED"


@pytest.mark.parametrize(
    "url,code",
    [
        ("postgresql://operator:sample@db.example/lyte", "VERIFIED_TLS_REQUIRED"),
        ("postgresql://operator:sample@db.example/lyte?sslmode=require", "VERIFIED_TLS_REQUIRED"),
        ("postgresql://operator:sample@localhost/lyte?sslmode=disable", "VERIFIED_TLS_REQUIRED"),
        ("postgresql://operator:sample@db.example/lyte?sslmode=verify-ca", "VERIFIED_TLS_REQUIRED"),
        (
            "postgresql://operator:sample@db.example/lyte?sslmode=verify-full",
            "EXPLICIT_TLS_ROOT_REQUIRED",
        ),
        (
            "postgresql://operator:sample@127.0.0.1/lyte?sslmode=disable&host=evil",
            "TARGET_QUERY_UNSUPPORTED",
        ),
        (
            "postgresql://operator:sample@127.0.0.1/lyte?sslmode=disable&port=6543",
            "TARGET_QUERY_UNSUPPORTED",
        ),
        (
            "postgresql://operator:sample@127.0.0.1/lyte?sslmode=disable&service=evil",
            "TARGET_QUERY_UNSUPPORTED",
        ),
        (
            "postgresql://operator:sample@127.0.0.1/lyte?sslmode=disable&options=-csearch_path=other",
            "TARGET_QUERY_UNSUPPORTED",
        ),
        (
            "postgresql://operator:sample@127.0.0.1/lyte?sslmode=disable&sslmode=verify-full",
            "TARGET_QUERY_UNSUPPORTED",
        ),
        (
            "postgresql://operator@127.0.0.1/lyte?sslmode=disable",
            "EXPLICIT_TARGET_IDENTITY_REQUIRED",
        ),
        (
            "postgresql://operator:sample@127.0.0.1,evil/lyte?sslmode=disable",
            "SINGLE_TCP_HOST_REQUIRED",
        ),
        ("sqlite:///somewhere", "POSTGRESQL_TARGET_REQUIRED"),
    ],
)
def test_target_rejects_insecure_or_ambiguous_connections(monkeypatch, url, code):
    monkeypatch.setenv(recovery.TARGET_ENV, url)
    monkeypatch.setattr(recovery, "create_engine", lambda *a, **k: pytest.fail("must not connect"))
    with pytest.raises(recovery.PreservationError, match=code):
        recovery._target()


def test_target_requires_environment_only_and_rejects_ambient_libpq(monkeypatch):
    monkeypatch.delenv(recovery.TARGET_ENV, raising=False)
    with pytest.raises(recovery.PreservationError, match="TARGET_ENV_REQUIRED"):
        recovery._target()
    monkeypatch.setenv(
        recovery.TARGET_ENV, "postgresql://operator:sample@127.0.0.1/lyte?sslmode=disable"
    )
    monkeypatch.setenv("PGHOSTADDR", "192.0.2.10")
    with pytest.raises(recovery.PreservationError, match="AMBIENT_LIBPQ_ENV_UNSUPPORTED"):
        recovery._target()


def test_verified_remote_target_config_and_separate_schema(monkeypatch, tmp_path):
    ca = tmp_path / "ca.pem"
    ca.write_text("SAMPLE configuration only; no TLS connection claimed")
    url = make_url("postgresql://operator:sample@db.example/lyte").update_query_dict(
        {"sslmode": "verify-full", "sslrootcert": str(ca)}
    )
    monkeypatch.setenv(recovery.TARGET_ENV, url.render_as_string(hide_password=False))
    monkeypatch.setenv(recovery.SCHEMA_ENV, "preserved")
    engine = recovery._target()
    args, kwargs = engine.dialect.create_connect_args(engine.url)
    assert kwargs["host"] == "db.example"
    assert kwargs["port"] == 5432
    assert kwargs["sslmode"] == "verify-full"
    engine.dispose()
    monkeypatch.setenv(recovery.SCHEMA_ENV, "public,other")
    with pytest.raises(recovery.PreservationError, match="TARGET_SCHEMA_INVALID"):
        recovery._target()


def test_remote_identity_requires_observed_tls_and_actual_schema(monkeypatch, tmp_path):
    monkeypatch.setenv(recovery.SCHEMA_ENV, "public")
    ca = tmp_path / "ca.pem"
    ca.write_text("SAMPLE configuration only; no TLS connection claimed")
    values = iter(["lyte", "public", "operator", "192.0.2.1", 5432])
    connection = SimpleNamespace(
        engine=SimpleNamespace(
            url=make_url(
                "postgresql://operator:sample@db.example/lyte?sslmode=verify-full"
            ).update_query_dict({"sslrootcert": str(ca)})
        ),
        scalar=lambda _query: next(values),
        connection=SimpleNamespace(
            driver_connection=SimpleNamespace(pgconn=SimpleNamespace(ssl_in_use=False))
        ),
    )
    with pytest.raises(recovery.PreservationError, match="VERIFIED_TLS_NOT_OBSERVED"):
        recovery._target_identity(connection)
    values = iter(["lyte", "other_schema", "operator", "192.0.2.1", 5432])
    connection.connection.driver_connection.pgconn.ssl_in_use = True
    with pytest.raises(recovery.PreservationError, match="CONNECTED_TARGET_IDENTITY_MISMATCH"):
        recovery._target_identity(connection)


def test_intent_must_be_flushed_before_restore_can_contact_target(source, tmp_path, monkeypatch):
    path, _, _ = source
    result = recovery.backup(path, tmp_path / "backup")
    monkeypatch.setattr(recovery.os, "fsync", lambda _fd: (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(recovery, "_target", lambda: pytest.fail("must not contact target"))
    with pytest.raises(OSError):
        recovery.restore(
            tmp_path / "backup/snapshot.sqlite",
            result["snapshot_sha256"],
            "a" * 64,
            tmp_path / "restore",
        )


@pytest.fixture
def postgres_target(monkeypatch):
    url = os.getenv("LYTE_TEST_POSTGRES_URL", "")
    if not url:
        pytest.skip("LYTE_TEST_POSTGRES_URL required for PostgreSQL preservation proof")
    admin = create_engine(url)
    schema = "preservation_" + uuid4().hex
    with admin.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    parsed = make_url(url).update_query_dict(
        {"options": f"-csearch_path={schema} -cTimeZone=Pacific/Honolulu"}
    )
    monkeypatch.setenv("DATABASE_URL", parsed.render_as_string(hide_password=False))
    monkeypatch.setenv("LYTE_ENV", "test")
    target_url = (
        make_url(url)
        .update_query_dict({"sslmode": "disable"})
        .render_as_string(hide_password=False)
    )
    monkeypatch.setenv(recovery.TARGET_ENV, target_url)
    monkeypatch.setenv(recovery.SCHEMA_ENV, schema)
    command.upgrade(Config("alembic.ini"), "head")
    engine = create_engine(parsed)
    yield engine
    engine.dispose()
    with admin.begin() as connection:
        connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
    admin.dispose()


def test_postgres_restore_preserves_hashes_scope_sequences_and_rejects_second_import(
    source, tmp_path, postgres_target
):
    path, _, scopes = source
    backup = recovery.backup(path, tmp_path / "backup")
    identity = recovery.inspect_target()["target_identity_sha256"]
    result = recovery.restore(
        tmp_path / "backup/snapshot.sqlite",
        backup["snapshot_sha256"],
        identity,
        tmp_path / "restored",
    )
    assert result["effect"] == "COMMITTED"
    assert result["summary"] == backup["summary"]
    database = Database(os.environ["DATABASE_URL"])
    try:
        store = LyteStore(database)
        for scope in scopes:
            assert all(store.verify_chain(scope, stream).valid for stream in STREAM_KINDS)
            assert len(store.list_receipts(scope)) == 2
    finally:
        database.dispose()
    with pytest.raises(recovery.PreservationError, match="TARGET_NOT_EMPTY"):
        recovery.restore(
            tmp_path / "backup/snapshot.sqlite",
            backup["snapshot_sha256"],
            identity,
            tmp_path / "second-import",
        )
    with postgres_target.connect() as connection:
        assert recovery.summarize(connection) == backup["summary"]


def test_postgres_failure_rolls_back_all_inserted_rows_and_identity_mismatch_never_imports(
    source,
    tmp_path,
    postgres_target,
    monkeypatch,
):
    path, _, _ = source
    result = recovery.backup(path, tmp_path / "backup")
    identity = recovery.inspect_target()["target_identity_sha256"]
    with pytest.raises(recovery.PreservationError, match="TARGET_IDENTITY_MISMATCH"):
        recovery.restore(
            tmp_path / "backup/snapshot.sqlite",
            result["snapshot_sha256"],
            "a" * 64,
            tmp_path / "wrong-target",
        )

    def fail_after_first_table(source_connection, target_connection):
        table = Base.metadata.tables["lyte_tenants"]
        rows = source_connection.execute(select(table)).mappings().all()
        target_connection.execute(table.insert(), [dict(row) for row in rows])
        raise recovery.PreservationError("INJECTED_IMPORT_FAILURE")

    monkeypatch.setattr(recovery, "_copy_rows", fail_after_first_table)
    with pytest.raises(recovery.PreservationError, match="INJECTED_IMPORT_FAILURE"):
        recovery.restore(
            tmp_path / "backup/snapshot.sqlite",
            result["snapshot_sha256"],
            identity,
            tmp_path / "rollback",
        )
    with postgres_target.connect() as connection:
        assert not any(row["rows"] for row in recovery.summarize(connection)["tables"].values())
    failure = json.loads((tmp_path / "rollback/failure.json").read_bytes())
    assert failure["effect"] == "NOT_COMMITTED"
    assert failure["truth_label"] == "BLOCKED"


def test_postgres_inspects_schema_only_after_all_locks_and_excludes_other_writers(
    source, tmp_path, postgres_target, monkeypatch
):
    path, _, _ = source
    result = recovery.backup(path, tmp_path / "backup")
    identity = recovery.inspect_target()["target_identity_sha256"]
    original_schema = recovery._schema
    inspections = []

    def locked_schema(connection, *, require_migration):
        if connection.dialect.name == "postgresql":
            locks = set(
                connection.execute(
                    text(
                        "SELECT c.relname FROM pg_locks l JOIN pg_class c ON c.oid=l.relation "
                        "WHERE l.pid=pg_backend_pid() "
                        "AND l.mode='AccessExclusiveLock' AND l.granted"
                    )
                ).scalars()
            )
            # Post-commit readback is deliberately read-only and has released write locks.
            if connection.scalar(text("SHOW transaction_read_only")) == "off":
                assert set(Base.metadata.tables) | {"alembic_version"} <= locks
                inspections.append(True)
                with postgres_target.connect() as other:
                    other.exec_driver_sql("SET LOCAL lock_timeout='100ms'")
                    with pytest.raises(OperationalError, match="lock timeout"):
                        other.exec_driver_sql("ALTER TABLE lyte_tenants ADD COLUMN raced TEXT")
        return original_schema(connection, require_migration=require_migration)

    monkeypatch.setattr(recovery, "_schema", locked_schema)
    restored = recovery.restore(
        tmp_path / "backup/snapshot.sqlite",
        result["snapshot_sha256"],
        identity,
        tmp_path / "restore",
    )
    assert inspections == [True, True]
    assert restored["effect"] == "COMMITTED"
