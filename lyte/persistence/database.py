"""Portable SQLAlchemy engine/session setup for SQLite and PostgreSQL."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy import Engine, create_engine, event, inspect, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from lyte.settings import Settings

from .models import Base

EXPECTED_SCHEMA_REVISIONS = ("20260904_0001",)


@dataclass(frozen=True, slots=True)
class SchemaReadiness:
    """Measured database-schema compatibility for one running release."""

    ready: bool
    state: str
    expected_revisions: tuple[str, ...]
    observed_revisions: tuple[str, ...]
    missing_tables: tuple[str, ...]
    validation_mode: str

    def to_dict(self) -> dict[str, object]:
        return {
            "ready": self.ready,
            "state": self.state,
            "expected_revisions": list(self.expected_revisions),
            "observed_revisions": list(self.observed_revisions),
            "missing_tables": list(self.missing_tables),
            "validation_mode": self.validation_mode,
            "truth_label": "MEASURED" if self.ready else "UNAVAILABLE",
        }


class Database:
    def __init__(self, settings_or_url: Settings | str) -> None:
        database_url = (
            settings_or_url.database_url
            if isinstance(settings_or_url, Settings)
            else settings_or_url
        )
        kwargs: dict[str, object] = {
            "pool_pre_ping": True,
            "future": True,
        }
        if database_url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
            if database_url.endswith(":memory:"):
                kwargs["poolclass"] = StaticPool
        self.engine: Engine = create_engine(database_url, **kwargs)
        if self.engine.dialect.name == "sqlite":
            event.listen(self.engine, "connect", self._enable_sqlite_foreign_keys)
        self.session_factory = sessionmaker(
            bind=self.engine,
            class_=Session,
            autoflush=False,
            expire_on_commit=False,
        )

    @staticmethod
    def _enable_sqlite_foreign_keys(dbapi_connection: object, connection_record: object) -> None:
        del connection_record
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self.session_factory()
        try:
            yield session
        finally:
            session.close()

    def create_schema(self) -> None:
        """Local/test convenience. Production uses Alembic migrations."""
        Base.metadata.create_all(self.engine)

    def ping(self) -> None:
        """Exercise the same session factory used by application persistence."""

        with self.session() as session:
            if session.execute(text("SELECT 1")).scalar_one() != 1:
                raise RuntimeError("database liveness probe returned an unexpected value")

    def schema_readiness(self, *, require_migration_revision: bool) -> SchemaReadiness:
        """Verify required tables and, in production, the exact Alembic head.

        Local/demo ``create_all`` schemas remain supported when no Alembic version
        table exists. An explicitly versioned but stale schema fails in every
        environment so a migration regression cannot be hidden by table presence.
        """

        required_tables = frozenset(Base.metadata.tables)
        with self.engine.connect() as connection:
            present_tables = frozenset(inspect(connection).get_table_names())
            missing_tables = tuple(sorted(required_tables - present_tables))
            observed_revisions: tuple[str, ...] = ()
            has_migration_table = "alembic_version" in present_tables
            if has_migration_table:
                observed_revisions = tuple(
                    sorted(
                        str(value)
                        for value in connection.execute(
                            text("SELECT version_num FROM alembic_version")
                        ).scalars()
                    )
                )

        mode = "ALEMBIC_EXACT_HEAD" if require_migration_revision else "LOCAL_SCHEMA"
        if missing_tables:
            state = "MISSING_REQUIRED_TABLES"
            ready = False
        elif has_migration_table and not observed_revisions:
            state = "MISSING_MIGRATION_REVISION"
            ready = False
        elif observed_revisions and observed_revisions != EXPECTED_SCHEMA_REVISIONS:
            state = "STALE_MIGRATION_REVISION"
            ready = False
        elif observed_revisions == EXPECTED_SCHEMA_REVISIONS:
            state = "READY_MIGRATED"
            ready = True
        elif require_migration_revision:
            state = "MISSING_MIGRATION_REVISION"
            ready = False
        else:
            state = "READY_LOCAL_SCHEMA"
            ready = True
        return SchemaReadiness(
            ready=ready,
            state=state,
            expected_revisions=EXPECTED_SCHEMA_REVISIONS,
            observed_revisions=observed_revisions,
            missing_tables=missing_tables,
            validation_mode=mode,
        )

    def dispose(self) -> None:
        self.engine.dispose()
