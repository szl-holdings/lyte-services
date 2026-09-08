"""SQLAlchemy 2.0 models for scoped, append-only enterprise state."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from lyte.domain import utc_now


class ImmutableRecordError(RuntimeError):
    """An append-only record was targeted by an update or delete."""


class Base(DeclarativeBase):
    pass


class TenantRecord(Base):
    __tablename__ = "lyte_tenants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class WorkspaceRecord(Base):
    __tablename__ = "lyte_workspaces"
    __table_args__ = (
        UniqueConstraint("tenant_id", "slug", name="uq_workspace_tenant_slug"),
        UniqueConstraint("tenant_id", "id", name="uq_workspace_tenant_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("lyte_tenants.id", ondelete="RESTRICT"), nullable=False
    )
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class StreamHeadRecord(Base):
    """Mutable serialization point; event rows themselves remain immutable."""

    __tablename__ = "lyte_stream_heads"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["lyte_workspaces.tenant_id", "lyte_workspaces.id"],
            ondelete="RESTRICT",
            name="fk_stream_head_scope",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    stream_kind: Mapped[str] = mapped_column(String(32), primary_key=True)
    last_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    last_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class AppendOnlyMixin:
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(36), nullable=False)
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    previous_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    record_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    basis_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


_SCOPE_FK = ForeignKeyConstraint(
    ["tenant_id", "workspace_id"],
    ["lyte_workspaces.tenant_id", "lyte_workspaces.id"],
    ondelete="RESTRICT",
    name="fk_append_scope",
)


class ReceiptRecord(AppendOnlyMixin, Base):
    __tablename__ = "lyte_receipts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["lyte_workspaces.tenant_id", "lyte_workspaces.id"],
            ondelete="RESTRICT",
            name="fk_receipt_scope",
        ),
        UniqueConstraint("tenant_id", "workspace_id", "sequence", name="uq_receipt_scope_sequence"),
        UniqueConstraint("tenant_id", "workspace_id", "record_hash", name="uq_receipt_scope_hash"),
        Index("ix_receipt_subject", "tenant_id", "workspace_id", "subject_type", "subject_id"),
    )

    kind: Mapped[str] = mapped_column(String(128), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(128), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(256), nullable=False)
    truth_label: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_refs: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)


class IdempotencyRecord(AppendOnlyMixin, Base):
    __tablename__ = "lyte_idempotency_records"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["lyte_workspaces.tenant_id", "lyte_workspaces.id"],
            ondelete="RESTRICT",
            name="fk_idempotency_scope",
        ),
        UniqueConstraint(
            "tenant_id", "workspace_id", "sequence", name="uq_idempotency_scope_sequence"
        ),
        UniqueConstraint(
            "tenant_id", "workspace_id", "record_hash", name="uq_idempotency_scope_hash"
        ),
        UniqueConstraint(
            "tenant_id", "workspace_id", "key_digest", name="uq_idempotency_scope_key"
        ),
    )

    key_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    response_receipt_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class MemoryRecord(AppendOnlyMixin, Base):
    __tablename__ = "lyte_memory_records"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["lyte_workspaces.tenant_id", "lyte_workspaces.id"],
            ondelete="RESTRICT",
            name="fk_memory_scope",
        ),
        UniqueConstraint("tenant_id", "workspace_id", "sequence", name="uq_memory_scope_sequence"),
        UniqueConstraint("tenant_id", "workspace_id", "record_hash", name="uq_memory_scope_hash"),
        Index(
            "ix_memory_scope_digest",
            "tenant_id",
            "workspace_id",
            "scope_digest",
            "sequence",
        ),
    )

    scope_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    memory_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    truth_label: Mapped[str] = mapped_column(String(32), nullable=False)
    evidence_refs: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    subjects: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    receipt_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)


class AnatomyTraceRecord(AppendOnlyMixin, Base):
    __tablename__ = "lyte_anatomy_traces"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["lyte_workspaces.tenant_id", "lyte_workspaces.id"],
            ondelete="RESTRICT",
            name="fk_anatomy_scope",
        ),
        UniqueConstraint("tenant_id", "workspace_id", "sequence", name="uq_anatomy_scope_sequence"),
        UniqueConstraint("tenant_id", "workspace_id", "record_hash", name="uq_anatomy_scope_hash"),
        Index("ix_anatomy_trace", "tenant_id", "workspace_id", "trace_id", "sequence"),
    )

    trace_id: Mapped[str] = mapped_column(String(36), nullable=False)
    span_id: Mapped[str] = mapped_column(String(36), nullable=False)
    parent_span_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    organ: Mapped[str] = mapped_column(String(32), nullable=False)
    operation: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    truth_label: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    input_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    output_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attributes_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class OperationalRecord(AppendOnlyMixin, Base):
    """One immutable version of a current operational entity projection."""

    __tablename__ = "lyte_operational_records"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["lyte_workspaces.tenant_id", "lyte_workspaces.id"],
            ondelete="RESTRICT",
            name="fk_operational_scope",
        ),
        UniqueConstraint(
            "tenant_id", "workspace_id", "sequence", name="uq_operational_scope_sequence"
        ),
        UniqueConstraint(
            "tenant_id", "workspace_id", "record_hash", name="uq_operational_scope_hash"
        ),
        UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "entity_kind",
            "entity_id",
            "version",
            name="uq_operational_entity_version",
        ),
        Index(
            "ix_operational_entity_latest",
            "tenant_id",
            "workspace_id",
            "entity_kind",
            "entity_id",
            "version",
        ),
    )

    entity_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(256), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    truth_label: Mapped[str] = mapped_column(String(32), nullable=False)
    body_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    evidence_refs: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    source_revision: Mapped[str | None] = mapped_column(String(64), nullable=True)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


IMMUTABLE_MODELS = (
    ReceiptRecord,
    IdempotencyRecord,
    MemoryRecord,
    AnatomyTraceRecord,
    OperationalRecord,
)


def _reject_update(mapper: Any, connection: Any, target: Any) -> None:
    del mapper, connection
    raise ImmutableRecordError(f"{type(target).__name__} is append-only and cannot be updated")


def _reject_delete(mapper: Any, connection: Any, target: Any) -> None:
    del mapper, connection
    raise ImmutableRecordError(f"{type(target).__name__} is append-only and cannot be deleted")


for _model in IMMUTABLE_MODELS:
    event.listen(_model, "before_update", _reject_update)
    event.listen(_model, "before_delete", _reject_delete)
