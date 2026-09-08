"""Portable SQLAlchemy engine/session setup for SQLite and PostgreSQL."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from lyte.settings import Settings

from .models import Base


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

    def dispose(self) -> None:
        self.engine.dispose()
