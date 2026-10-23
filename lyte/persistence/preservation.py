"""Operator-only SQLite preservation and transactional PostgreSQL import.

No application route or provider control is exposed. Target credentials are read
only from an environment variable; receipts contain hashes/counts, never rows or
connection strings. A snapshot proves its observed contents, not a later cutover.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import sqlite3
import time
from collections.abc import Mapping
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, Engine, create_engine, inspect, select, text
from sqlalchemy.engine import make_url

from lyte.domain import isoformat_z, sha256_json
from lyte.persistence.database import EXPECTED_SCHEMA_REVISIONS
from lyte.persistence.models import Base

STREAM_TABLES = {
    "receipts": "lyte_receipts",
    "idempotency": "lyte_idempotency_records",
    "memory": "lyte_memory_records",
    "anatomy": "lyte_anatomy_traces",
    "operational": "lyte_operational_records",
}
TARGET_ENV = "LYTE_MIGRATION_TARGET_URL"
SCHEMA_ENV = "LYTE_MIGRATION_SCHEMA"


class PreservationError(RuntimeError):
    """Safe fixed error codes; do not include row contents or credentials."""


def canonical(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode()


def file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _normalize(value: Any) -> Any:
    if isinstance(value, datetime):
        # SQLite stores Lyte UTC instants without a timezone; PostgreSQL retains it.
        return value.replace(tzinfo=value.tzinfo or UTC).astimezone(UTC).isoformat()
    if isinstance(value, Mapping):
        return {key: _normalize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    return value


def _private_file(path: Path, data: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    _sync_directory(path.parent)


def _sync_directory(path: Path) -> None:
    # Windows has no portable directory fsync. Do not claim crash-durable receipts there.
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _receipt(root: Path, name: str, body: dict[str, Any]) -> str:
    body = {
        "schema": "szl.lyte.preservation/v1",
        "observed_at": datetime.now(UTC).isoformat(),
        "tool_sha256": file_hash(Path(__file__)),
        "signed": False,
        "signature_status": "UNAVAILABLE",
        "cutover_authorized": False,
        "directory_fsync": "UNAVAILABLE" if os.name == "nt" else "MEASURED",
        **body,
    }
    data = canonical(body)
    _private_file(root / name, data)
    return hashlib.sha256(data).hexdigest()


def _sqlite_engine(path: Path) -> Engine:
    uri = path.resolve().as_uri() + "?mode=ro&immutable=1"
    return create_engine("sqlite+pysqlite://", creator=lambda: sqlite3.connect(uri, uri=True))


def _schema(connection: Connection, *, require_migration: bool) -> None:
    inspector = inspect(connection)
    actual = set(inspector.get_table_names())
    expected = set(Base.metadata.tables)
    if actual - {"alembic_version"} != expected:
        raise PreservationError("SCHEMA_TABLE_SET_MISMATCH")
    if inspector.get_view_names():
        raise PreservationError("SCHEMA_VIEWS_UNSUPPORTED")
    for table in Base.metadata.sorted_tables:
        if {column["name"] for column in inspector.get_columns(table.name)} != set(table.c.keys()):
            raise PreservationError("SCHEMA_COLUMN_SET_MISMATCH")
    revisions = ()
    if "alembic_version" in actual:
        revisions = tuple(
            sorted(connection.execute(text("SELECT version_num FROM alembic_version")).scalars())
        )
        if revisions != EXPECTED_SCHEMA_REVISIONS:
            raise PreservationError("ALEMBIC_REVISION_MISMATCH")
    if require_migration and revisions != EXPECTED_SCHEMA_REVISIONS:
        raise PreservationError("ALEMBIC_REVISION_REQUIRED")


def _timestamp(value: datetime | None) -> str | None:
    return isoformat_z(value.replace(tzinfo=value.tzinfo or UTC)) if value is not None else None


def _projection(row: Mapping[str, Any], stream: str) -> dict[str, Any]:
    """Reconstruct every hashed projection field, including timestamps and payloads."""
    common = {"tenant_id": row["tenant_id"], "workspace_id": row["workspace_id"]}
    columns = {
        "receipts": (
            "szl.lyte.receipt/v1",
            "kind subject_type subject_id truth_label payload_sha256 evidence_refs",
            {"payload": "payload_json"},
        ),
        "idempotency": (
            "szl.lyte.idempotency-record/v1",
            "key_digest request_digest response_receipt_hash",
            {},
        ),
        "memory": (
            "szl.lyte.memory-record/v2",
            "memory_kind summary truth_label evidence_refs subjects receipt_hash",
            {"partition_digest": "scope_digest", "metadata": "metadata_json"},
        ),
        "anatomy": (
            "szl.lyte.anatomy-trace/v1",
            "trace_id span_id parent_span_id organ operation state truth_label "
            "input_sha256 output_sha256",
            {"attributes": "attributes_json"},
        ),
        "operational": (
            "szl.lyte.operational-record/v1",
            "entity_kind entity_id version name truth_label evidence_refs source_revision",
            {"body": "body_json", "metadata": "metadata_json"},
        ),
    }
    schema, names, aliases = columns[stream]
    data = {name: row[name] for name in names.split()}
    data.update({key: row[column] for key, column in aliases.items()})
    if stream == "receipts" and sha256_json(row["payload_json"]) != row["payload_sha256"]:
        raise PreservationError("PAYLOAD_DIGEST_MISMATCH")
    if stream == "anatomy":
        data.update(common)
        data.update({name: _timestamp(row[name]) for name in ("started_at", "completed_at")})
    if stream == "operational":
        data["observed_at"] = _timestamp(row["observed_at"])
    return {
        "schema": schema,
        **common,
        "stream_kind": stream,
        "sequence": row["sequence"],
        "previous_hash": row["previous_hash"],
        "created_at": _timestamp(row["created_at"]),
        "data": data,
    }


def _chains(connection: Connection) -> int:
    workspaces = Base.metadata.tables["lyte_workspaces"]
    scopes = set(connection.execute(select(workspaces.c.tenant_id, workspaces.c.id)))
    heads_table = Base.metadata.tables["lyte_stream_heads"]
    heads = {
        (row["tenant_id"], row["workspace_id"], row["stream_kind"]): row
        for row in connection.execute(select(heads_table)).mappings()
    }
    expected_keys = {
        (tenant, workspace, stream) for tenant, workspace in scopes for stream in STREAM_TABLES
    }
    if set(heads) != expected_keys:
        raise PreservationError("STREAM_HEAD_SET_MISMATCH")
    for stream, name in STREAM_TABLES.items():
        table = Base.metadata.tables[name]
        progress: dict[tuple[str, str], tuple[int, str | None]] = {}
        statement = select(table).order_by(
            table.c.tenant_id, table.c.workspace_id, table.c.sequence
        )
        for row in connection.execute(statement).mappings():
            scope = (row["tenant_id"], row["workspace_id"])
            sequence, previous = progress.get(scope, (0, None))
            basis = row["basis_json"]
            if (
                scope not in scopes
                or row["sequence"] != sequence + 1
                or row["previous_hash"] != previous
                or not isinstance(basis, dict)
                or basis != _projection(row, stream)
                or sha256_json(basis) != row["record_hash"]
            ):
                raise PreservationError("HASH_CHAIN_MISMATCH")
            progress[scope] = (sequence + 1, row["record_hash"])
        for scope in scopes:
            head = heads[(*scope, stream)]
            if progress.get(scope, (0, None)) != (head["last_sequence"], head["last_hash"]):
                raise PreservationError("STREAM_HEAD_MISMATCH")
    return len(expected_keys)


def summarize(connection: Connection, *, require_migration: bool = False) -> dict[str, Any]:
    """Read every supported table; reject unknown data rather than silently omit it."""
    _schema(connection, require_migration=require_migration)
    if connection.dialect.name == "sqlite":
        if connection.exec_driver_sql("PRAGMA integrity_check").scalars().all() != ["ok"]:
            raise PreservationError("SQLITE_INTEGRITY_FAILED")
        if connection.exec_driver_sql("PRAGMA foreign_key_check").first() is not None:
            raise PreservationError("FOREIGN_KEY_CHECK_FAILED")
    tables = {}
    for table in Base.metadata.sorted_tables:
        digest = hashlib.sha256()
        count = 0
        for row in connection.execute(
            select(table).order_by(*table.primary_key.columns)
        ).mappings():
            digest.update(canonical(_normalize(dict(row))))
            count += 1
        tables[table.name] = {"rows": count, "sha256": digest.hexdigest()}
    return {
        "tables": tables,
        "logical_sha256": hashlib.sha256(canonical(tables)).hexdigest(),
        "verified_streams": _chains(connection),
    }


def backup(source: Path, output: Path, *, timeout_seconds: float = 120) -> dict[str, Any]:
    """Use SQLite's online backup API, including committed WAL contents."""
    if not source.is_file():
        raise PreservationError("SOURCE_FILE_REQUIRED")
    output.mkdir(mode=0o700)  # Exclusive: never overwrite an existing recovery bundle.
    _sync_directory(output.parent)
    intent = _receipt(output, "intent.json", {"operation": "backup", "truth_label": "DECLARED"})
    snapshot = output / "snapshot.sqlite"
    try:
        _private_file(snapshot, b"")
        deadline = time.monotonic() + timeout_seconds

        def progress(_status: int, _remaining: int, _total: int) -> None:
            if time.monotonic() > deadline:
                raise PreservationError("BACKUP_TIMEOUT")

        uri = source.resolve().as_uri() + "?mode=ro"
        with (
            closing(sqlite3.connect(uri, uri=True)) as original,
            closing(sqlite3.connect(snapshot)) as copy,
        ):
            original.backup(copy, pages=256, progress=progress, sleep=0.1)
            copy.execute("PRAGMA journal_mode=DELETE")
        with snapshot.open("r+b") as stream:
            os.fsync(stream.fileno())
        _sync_directory(output)
        engine = _sqlite_engine(snapshot)
        try:
            with engine.connect() as connection:
                summary = summarize(connection)
        finally:
            engine.dispose()
        result = {
            "operation": "backup",
            "truth_label": "MEASURED",
            "effect": "SNAPSHOT_WRITTEN",
            "snapshot_sha256": file_hash(snapshot),
            "snapshot_bytes": snapshot.stat().st_size,
            "summary": summary,
            "previous_receipt_sha256": intent,
        }
        _receipt(output, "result.json", result)
        return result
    except Exception as exc:
        _receipt(
            output,
            "failure.json",
            {
                "operation": "backup",
                "truth_label": "BLOCKED",
                "effect": "SNAPSHOT_NOT_QUALIFIED",
                "error_code": _error_code(exc),
                "previous_receipt_sha256": intent,
            },
        )
        raise


def _target() -> Engine:
    value = os.environ.get(TARGET_ENV, "")
    if not value:
        raise PreservationError("TARGET_ENV_REQUIRED")
    # libpq environment/service defaults must not redirect an approved target or
    # downgrade its transport. All connection identity comes from this one URL.
    if any(name.upper().startswith("PG") for name in os.environ):
        raise PreservationError("AMBIENT_LIBPQ_ENV_UNSUPPORTED")
    url = make_url(value)
    if url.drivername not in {"postgresql", "postgresql+psycopg"}:
        raise PreservationError("POSTGRESQL_TARGET_REQUIRED")
    if not all((url.host, url.username, url.password, url.database)):
        raise PreservationError("EXPLICIT_TARGET_IDENTITY_REQUIRED")
    if any(character in url.host for character in ",/\\ \t\r\n"):
        raise PreservationError("SINGLE_TCP_HOST_REQUIRED")
    allowed = {"sslmode", "sslrootcert", "sslcert", "sslkey"}
    if set(url.query) - allowed or any(not isinstance(item, str) for item in url.query.values()):
        raise PreservationError("TARGET_QUERY_UNSUPPORTED")
    # Only literal loopback addresses qualify for local plaintext testing; a
    # hostname such as localhost can resolve somewhere else.
    try:
        loopback = ipaddress.ip_address(url.host).is_loopback
    except ValueError:
        loopback = False
    sslmode = url.query.get("sslmode")
    if sslmode != "verify-full" and not (loopback and sslmode == "disable"):
        raise PreservationError("VERIFIED_TLS_REQUIRED")
    if sslmode == "verify-full" and not Path(url.query.get("sslrootcert", "")).is_file():
        raise PreservationError("EXPLICIT_TLS_ROOT_REQUIRED")
    if ("sslcert" in url.query) != ("sslkey" in url.query):
        raise PreservationError("TLS_CLIENT_CERT_PAIR_REQUIRED")
    schema = _target_schema()
    return create_engine(
        url.set(drivername="postgresql+psycopg", port=url.port or 5432),
        hide_parameters=True,
        connect_args={
            "connect_timeout": 15,
            "gssencmode": "disable",
            "options": f"-csearch_path={schema} -cTimeZone=UTC",
        },
    )


def _target_schema() -> str:
    schema = os.environ.get(SCHEMA_ENV, "public")
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", schema) or schema.startswith("pg_"):
        raise PreservationError("TARGET_SCHEMA_INVALID")
    return schema


def _target_identity(connection: Connection) -> str:
    url = connection.engine.url
    identity = {
        "host": url.host,
        "port": url.port or 5432,
        "database": connection.scalar(text("SELECT current_database()")),
        "schema": connection.scalar(text("SELECT current_schema()")),
        "user": connection.scalar(text("SELECT current_user")),
        "server_address": connection.scalar(text("SELECT inet_server_addr()::text")),
        "server_port": connection.scalar(text("SELECT inet_server_port()")),
        "sslmode": url.query["sslmode"],
        "tls_root_sha256": (
            file_hash(Path(url.query["sslrootcert"]))
            if url.query["sslmode"] == "verify-full"
            else None
        ),
    }
    if (
        identity["database"] != url.database
        or identity["user"] != url.username
        or identity["schema"] != _target_schema()
    ):
        raise PreservationError("CONNECTED_TARGET_IDENTITY_MISMATCH")
    if (
        url.query["sslmode"] == "verify-full"
        and not connection.connection.driver_connection.pgconn.ssl_in_use
    ):
        raise PreservationError("VERIFIED_TLS_NOT_OBSERVED")
    return hashlib.sha256(canonical(identity)).hexdigest()


def inspect_target() -> dict[str, Any]:
    """Read-only report; no receipt or database change is made."""
    engine = _target()
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            return {
                "truth_label": "MEASURED",
                "target_identity_sha256": _target_identity(connection),
                "summary": summarize(connection, require_migration=True),
            }
    finally:
        engine.dispose()


def _copy_rows(source: Connection, target: Connection) -> None:
    for table in Base.metadata.sorted_tables:
        rows = source.execute(select(table).order_by(*table.primary_key.columns)).mappings()
        for batch in rows.partitions(500):
            target.execute(table.insert(), [dict(row) for row in batch])


def restore(
    snapshot: Path, expected_sha256: str, target_identity: str, output: Path
) -> dict[str, Any]:
    """Import a qualified snapshot into an empty migrated PostgreSQL schema.

    Tables are exclusively locked; any import or comparison failure rolls back.
    A commit error is honestly an unknown outcome and must not be blindly retried.
    """
    if not all(
        re.fullmatch(r"[0-9a-f]{64}", value) for value in (expected_sha256, target_identity)
    ):
        raise PreservationError("EXACT_DIGEST_REQUIRED")
    output.mkdir(mode=0o700)
    _sync_directory(output.parent)
    intent = _receipt(
        output,
        "intent.json",
        {
            "operation": "restore",
            "truth_label": "DECLARED",
            "snapshot_sha256": expected_sha256,
            "target_identity_sha256": target_identity,
        },
    )
    effect = "NOT_COMMITTED"
    source_engine = target_engine = None
    try:
        # Own a frozen copy; never import from a live WAL file or a changing input.
        if any(Path(str(snapshot) + suffix).exists() for suffix in ("-wal", "-journal")):
            raise PreservationError("STANDALONE_SNAPSHOT_REQUIRED")
        frozen = output / "input.sqlite"
        descriptor = os.open(frozen, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as copy, snapshot.open("rb") as original:
            while chunk := original.read(1024 * 1024):
                copy.write(chunk)
            copy.flush()
            os.fsync(copy.fileno())
        _sync_directory(output)
        if file_hash(frozen) != expected_sha256:
            raise PreservationError("SNAPSHOT_DIGEST_MISMATCH")
        source_engine = _sqlite_engine(frozen)
        with source_engine.connect() as source:
            expected = summarize(source)
            target_engine = _target()
            with target_engine.connect() as target:
                transaction = target.begin()
                try:
                    target.exec_driver_sql("SET LOCAL lock_timeout = '5s'")
                    target.exec_driver_sql("SET LOCAL TIME ZONE 'UTC'")
                    if _target_identity(target) != target_identity:
                        raise PreservationError("TARGET_IDENTITY_MISMATCH")
                    # Lock the fixed schema objects before inspecting their shape
                    # or revision; DDL/row writers cannot race that inspection.
                    for name in ["alembic_version", *sorted(Base.metadata.tables)]:
                        target.exec_driver_sql(
                            f'LOCK TABLE "{_target_schema()}"."{name}" '
                            "IN ACCESS EXCLUSIVE MODE NOWAIT"
                        )
                    before = summarize(target, require_migration=True)
                    if any(item["rows"] for item in before["tables"].values()):
                        raise PreservationError("TARGET_NOT_EMPTY")
                    _copy_rows(source, target)
                    if summarize(target, require_migration=True) != expected:
                        raise PreservationError("TRANSACTION_READBACK_MISMATCH")
                    effect = "COMMIT_OUTCOME_UNKNOWN"
                    transaction.commit()
                    effect = "COMMITTED"
                except Exception:
                    if transaction.is_active:
                        transaction.rollback()
                    raise
            with target_engine.connect() as target:
                target.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                if _target_identity(target) != target_identity:
                    raise PreservationError("POST_COMMIT_TARGET_IDENTITY_MISMATCH")
                if summarize(target, require_migration=True) != expected:
                    raise PreservationError("POST_COMMIT_READBACK_MISMATCH")
        result = {
            "operation": "restore",
            "truth_label": "MEASURED",
            "effect": effect,
            "snapshot_sha256": expected_sha256,
            "target_identity_sha256": target_identity,
            "summary": expected,
            "previous_receipt_sha256": intent,
        }
        _receipt(output, "result.json", result)
        return result
    except Exception as exc:
        _receipt(
            output,
            "failure.json",
            {
                "operation": "restore",
                "truth_label": "BLOCKED",
                "effect": effect,
                "error_code": _error_code(exc),
                "previous_receipt_sha256": intent,
            },
        )
        raise
    finally:
        for engine in (source_engine, target_engine):
            if engine is not None:
                engine.dispose()


def _error_code(exc: Exception) -> str:
    return str(exc) if isinstance(exc, PreservationError) else type(exc).__name__


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    save = commands.add_parser("backup")
    save.add_argument("--source", type=Path, required=True)
    save.add_argument("--output", type=Path, required=True)
    commands.add_parser("inspect-target")
    load = commands.add_parser("restore")
    load.add_argument("--snapshot", type=Path, required=True)
    load.add_argument("--snapshot-sha256", required=True)
    load.add_argument("--target-identity-sha256", required=True)
    load.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "backup":
            result = backup(args.source, args.output)
        elif args.command == "inspect-target":
            result = inspect_target()
        else:
            result = restore(
                args.snapshot, args.snapshot_sha256, args.target_identity_sha256, args.output
            )
    except Exception as exc:
        print(json.dumps({"truth_label": "BLOCKED", "error_code": _error_code(exc)}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
