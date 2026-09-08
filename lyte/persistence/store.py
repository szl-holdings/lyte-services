"""Tenant-scoped append-only repositories and hash-chain verification."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, TypeVar
from uuid import uuid4

from sqlalchemy import and_, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from lyte.domain import (
    AnatomyTrace,
    OperationalEntityKind,
    OperationalRecordDraft,
    ReceiptDraft,
    Scope,
    TenantSpec,
    WorkspaceSpec,
    isoformat_z,
    sha256_json,
    sha256_text,
    utc_now,
)
from lyte.governance.second_brain import MemoryDraft

from .database import Database
from .models import (
    AnatomyTraceRecord,
    IdempotencyRecord,
    MemoryRecord,
    OperationalRecord,
    ReceiptRecord,
    StreamHeadRecord,
    TenantRecord,
    WorkspaceRecord,
)

STREAM_RECEIPTS = "receipts"
STREAM_IDEMPOTENCY = "idempotency"
STREAM_MEMORY = "memory"
STREAM_ANATOMY = "anatomy"
STREAM_OPERATIONAL = "operational"
STREAM_KINDS = (
    STREAM_RECEIPTS,
    STREAM_IDEMPOTENCY,
    STREAM_MEMORY,
    STREAM_ANATOMY,
    STREAM_OPERATIONAL,
)


class PersistenceError(RuntimeError):
    pass


class ScopeNotFound(PersistenceError):
    pass


class IdempotencyConflict(PersistenceError):
    pass


class OperationalConflict(PersistenceError):
    pass


@dataclass(frozen=True, slots=True)
class ChainVerification:
    stream_kind: str
    valid: bool
    record_count: int
    head_hash: str | None
    error: str | None = None


AppendRecord = TypeVar(
    "AppendRecord", ReceiptRecord, IdempotencyRecord, MemoryRecord, AnatomyTraceRecord
)


class LyteStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create_tenant(self, spec: TenantSpec) -> TenantRecord:
        row = TenantRecord(id=str(spec.id), slug=spec.slug, name=spec.name, created_at=utc_now())
        with self.database.session() as session, session.begin():
            session.add(row)
        return row

    def create_workspace(self, spec: WorkspaceSpec) -> WorkspaceRecord:
        row = WorkspaceRecord(
            id=str(spec.id),
            tenant_id=str(spec.tenant_id),
            slug=spec.slug,
            name=spec.name,
            created_at=utc_now(),
        )
        with self.database.session() as session, session.begin():
            if session.get(TenantRecord, str(spec.tenant_id)) is None:
                raise ScopeNotFound("tenant does not exist")
            session.add(row)
            session.flush()
            for stream_kind in STREAM_KINDS:
                session.add(
                    StreamHeadRecord(
                        tenant_id=str(spec.tenant_id),
                        workspace_id=str(spec.id),
                        stream_kind=stream_kind,
                        last_sequence=0,
                        last_hash=None,
                        updated_at=utc_now(),
                    )
                )
        return row

    def _assert_scope(self, session: Session, scope: Scope) -> None:
        row = session.get(WorkspaceRecord, str(scope.workspace_id))
        if row is None or row.tenant_id != str(scope.tenant_id):
            raise ScopeNotFound("workspace does not exist in the requested tenant")

    def _locked_head(self, session: Session, scope: Scope, stream_kind: str) -> StreamHeadRecord:
        if stream_kind not in STREAM_KINDS:
            raise ValueError(f"unknown stream kind: {stream_kind}")
        statement = (
            select(StreamHeadRecord)
            .where(
                StreamHeadRecord.tenant_id == str(scope.tenant_id),
                StreamHeadRecord.workspace_id == str(scope.workspace_id),
                StreamHeadRecord.stream_kind == stream_kind,
            )
            .with_for_update()
        )
        head = session.scalar(statement)
        if head is None:
            raise ScopeNotFound("workspace stream heads have not been initialized")
        return head

    @staticmethod
    def _basis(
        *,
        schema: str,
        scope: Scope,
        stream_kind: str,
        sequence: int,
        previous_hash: str | None,
        created_at: datetime,
        data: Mapping[str, Any],
    ) -> dict[str, Any]:
        return {
            "schema": schema,
            "tenant_id": str(scope.tenant_id),
            "workspace_id": str(scope.workspace_id),
            "stream_kind": stream_kind,
            "sequence": sequence,
            "previous_hash": previous_hash,
            "created_at": isoformat_z(created_at),
            "data": dict(data),
        }

    @staticmethod
    def _advance_head(
        head: StreamHeadRecord, sequence: int, record_hash: str, created_at: datetime
    ) -> None:
        head.last_sequence = sequence
        head.last_hash = record_hash
        head.updated_at = created_at

    @staticmethod
    def _idempotency_digest(scope: Scope, key: str) -> str:
        if not isinstance(key, str) or not 16 <= len(key) <= 256 or key.isspace():
            raise ValueError("idempotency key must contain 16-256 characters")
        return sha256_text(
            f"szl.lyte.idempotency/v1\x00{scope.tenant_id}\x00{scope.workspace_id}\x00{key}"
        )

    @staticmethod
    def _receipt_request_digest(draft: ReceiptDraft) -> str:
        return sha256_json(
            {
                "kind": draft.kind,
                "subject_type": draft.subject_type,
                "subject_id": draft.subject_id,
                "payload": dict(draft.payload),
                "truth_label": draft.truth_label.value,
                "evidence_refs": list(draft.evidence_refs),
            }
        )

    def append_receipt(
        self,
        scope: Scope,
        draft: ReceiptDraft,
        *,
        idempotency_key: str | None = None,
        created_at: datetime | None = None,
    ) -> ReceiptRecord:
        at = created_at or utc_now()
        request_digest = self._receipt_request_digest(draft)
        key_digest = (
            self._idempotency_digest(scope, idempotency_key)
            if idempotency_key is not None
            else None
        )
        try:
            with self.database.session() as session, session.begin():
                self._assert_scope(session, scope)
                if key_digest:
                    existing = session.scalar(
                        select(IdempotencyRecord).where(
                            IdempotencyRecord.tenant_id == str(scope.tenant_id),
                            IdempotencyRecord.workspace_id == str(scope.workspace_id),
                            IdempotencyRecord.key_digest == key_digest,
                        )
                    )
                    if existing:
                        return self._resolve_idempotent_receipt(
                            session, scope, existing, request_digest
                        )

                head = self._locked_head(session, scope, STREAM_RECEIPTS)
                sequence = head.last_sequence + 1
                data = {
                    "kind": draft.kind,
                    "subject_type": draft.subject_type,
                    "subject_id": draft.subject_id,
                    "truth_label": draft.truth_label.value,
                    "payload": dict(draft.payload),
                    "payload_sha256": sha256_json(draft.payload),
                    "evidence_refs": list(draft.evidence_refs),
                }
                basis = self._basis(
                    schema="szl.lyte.receipt/v1",
                    scope=scope,
                    stream_kind=STREAM_RECEIPTS,
                    sequence=sequence,
                    previous_hash=head.last_hash,
                    created_at=at,
                    data=data,
                )
                receipt = ReceiptRecord(
                    id=str(uuid4()),
                    tenant_id=str(scope.tenant_id),
                    workspace_id=str(scope.workspace_id),
                    sequence=sequence,
                    previous_hash=head.last_hash,
                    record_hash=sha256_json(basis),
                    basis_json=basis,
                    created_at=at,
                    kind=draft.kind,
                    subject_type=draft.subject_type,
                    subject_id=draft.subject_id,
                    truth_label=draft.truth_label.value,
                    payload_json=dict(draft.payload),
                    payload_sha256=data["payload_sha256"],
                    evidence_refs=list(draft.evidence_refs),
                )
                session.add(receipt)
                self._advance_head(head, sequence, receipt.record_hash, at)
                if key_digest:
                    self._append_idempotency(
                        session,
                        scope,
                        key_digest=key_digest,
                        request_digest=request_digest,
                        response_receipt_hash=receipt.record_hash,
                        created_at=at,
                    )
                session.flush()
                return receipt
        except IntegrityError as exc:
            if key_digest:
                with self.database.session() as session:
                    existing = session.scalar(
                        select(IdempotencyRecord).where(
                            IdempotencyRecord.tenant_id == str(scope.tenant_id),
                            IdempotencyRecord.workspace_id == str(scope.workspace_id),
                            IdempotencyRecord.key_digest == key_digest,
                        )
                    )
                    if existing:
                        return self._resolve_idempotent_receipt(
                            session, scope, existing, request_digest
                        )
            raise PersistenceError("receipt append conflicted with concurrent state") from exc

    def _resolve_idempotent_receipt(
        self,
        session: Session,
        scope: Scope,
        record: IdempotencyRecord,
        request_digest: str,
    ) -> ReceiptRecord:
        if record.request_digest != request_digest:
            raise IdempotencyConflict("idempotency key was already used for a different request")
        receipt = session.scalar(
            select(ReceiptRecord).where(
                ReceiptRecord.tenant_id == str(scope.tenant_id),
                ReceiptRecord.workspace_id == str(scope.workspace_id),
                ReceiptRecord.record_hash == record.response_receipt_hash,
            )
        )
        if receipt is None:
            raise PersistenceError("idempotency record references a missing receipt")
        return receipt

    def get_receipt(self, scope: Scope, record_hash: str) -> ReceiptRecord | None:
        """Read a receipt only through its tenant/workspace scope."""
        if len(record_hash) != 64 or any(
            character not in "0123456789abcdef" for character in record_hash
        ):
            raise ValueError("record_hash must be a lowercase SHA-256 digest")
        with self.database.session() as session:
            self._assert_scope(session, scope)
            return session.scalar(
                select(ReceiptRecord).where(
                    ReceiptRecord.tenant_id == str(scope.tenant_id),
                    ReceiptRecord.workspace_id == str(scope.workspace_id),
                    ReceiptRecord.record_hash == record_hash,
                )
            )

    def list_receipts(
        self, scope: Scope, *, limit: int = 100, offset: int = 0
    ) -> list[ReceiptRecord]:
        """Page receipts in stable chain order inside one mandatory scope."""
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 100_000:
            raise ValueError("offset must be between 0 and 100000")
        with self.database.session() as session:
            self._assert_scope(session, scope)
            return list(
                session.scalars(
                    select(ReceiptRecord)
                    .where(
                        ReceiptRecord.tenant_id == str(scope.tenant_id),
                        ReceiptRecord.workspace_id == str(scope.workspace_id),
                    )
                    .order_by(ReceiptRecord.sequence)
                    .offset(offset)
                    .limit(limit)
                )
            )

    def _append_idempotency(
        self,
        session: Session,
        scope: Scope,
        *,
        key_digest: str,
        request_digest: str,
        response_receipt_hash: str,
        created_at: datetime,
    ) -> IdempotencyRecord:
        head = self._locked_head(session, scope, STREAM_IDEMPOTENCY)
        sequence = head.last_sequence + 1
        data = {
            "key_digest": key_digest,
            "request_digest": request_digest,
            "response_receipt_hash": response_receipt_hash,
        }
        basis = self._basis(
            schema="szl.lyte.idempotency-record/v1",
            scope=scope,
            stream_kind=STREAM_IDEMPOTENCY,
            sequence=sequence,
            previous_hash=head.last_hash,
            created_at=created_at,
            data=data,
        )
        row = IdempotencyRecord(
            id=str(uuid4()),
            tenant_id=str(scope.tenant_id),
            workspace_id=str(scope.workspace_id),
            sequence=sequence,
            previous_hash=head.last_hash,
            record_hash=sha256_json(basis),
            basis_json=basis,
            created_at=created_at,
            **data,
        )
        session.add(row)
        self._advance_head(head, sequence, row.record_hash, created_at)
        return row

    def append_memory(
        self,
        scope: Scope,
        draft: MemoryDraft,
        *,
        receipt_hash: str | None = None,
        created_at: datetime | None = None,
    ) -> MemoryRecord:
        at = created_at or utc_now()
        if receipt_hash is not None and (
            len(receipt_hash) != 64 or any(ch not in "0123456789abcdef" for ch in receipt_hash)
        ):
            raise ValueError("receipt_hash must be a lowercase SHA-256 digest")
        with self.database.session() as session, session.begin():
            self._assert_scope(session, scope)
            if (
                receipt_hash
                and session.scalar(
                    select(ReceiptRecord.id).where(
                        ReceiptRecord.tenant_id == str(scope.tenant_id),
                        ReceiptRecord.workspace_id == str(scope.workspace_id),
                        ReceiptRecord.record_hash == receipt_hash,
                    )
                )
                is None
            ):
                raise PersistenceError("memory receipt_hash does not exist in this scope")
            head = self._locked_head(session, scope, STREAM_MEMORY)
            sequence = head.last_sequence + 1
            data = {
                "scope_digest": draft.scope_digest,
                "memory_kind": draft.kind.value,
                "summary": draft.summary,
                "truth_label": draft.truth_label.value,
                "evidence_refs": list(draft.evidence_refs),
                "subjects": list(draft.subjects),
                "metadata": dict(draft.metadata),
                "receipt_hash": receipt_hash,
            }
            basis = self._basis(
                schema="szl.lyte.memory-record/v1",
                scope=scope,
                stream_kind=STREAM_MEMORY,
                sequence=sequence,
                previous_hash=head.last_hash,
                created_at=at,
                data=data,
            )
            row = MemoryRecord(
                id=str(uuid4()),
                tenant_id=str(scope.tenant_id),
                workspace_id=str(scope.workspace_id),
                sequence=sequence,
                previous_hash=head.last_hash,
                record_hash=sha256_json(basis),
                basis_json=basis,
                created_at=at,
                scope_digest=draft.scope_digest,
                memory_kind=draft.kind.value,
                summary=draft.summary,
                truth_label=draft.truth_label.value,
                evidence_refs=list(draft.evidence_refs),
                subjects=list(draft.subjects),
                metadata_json=dict(draft.metadata),
                receipt_hash=receipt_hash,
            )
            session.add(row)
            self._advance_head(head, sequence, row.record_hash, at)
            session.flush()
            return row

    def list_memory(
        self, scope: Scope, *, scope_digest: str, limit: int = 100
    ) -> list[MemoryRecord]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        with self.database.session() as session:
            self._assert_scope(session, scope)
            statement = (
                select(MemoryRecord)
                .where(
                    MemoryRecord.tenant_id == str(scope.tenant_id),
                    MemoryRecord.workspace_id == str(scope.workspace_id),
                    MemoryRecord.scope_digest == scope_digest,
                )
                .order_by(MemoryRecord.sequence.desc())
                .limit(limit)
            )
            return list(session.scalars(statement))

    def append_anatomy_trace(
        self, trace: AnatomyTrace, *, created_at: datetime | None = None
    ) -> AnatomyTraceRecord:
        at = created_at or utc_now()
        scope = trace.scope
        with self.database.session() as session, session.begin():
            self._assert_scope(session, scope)
            head = self._locked_head(session, scope, STREAM_ANATOMY)
            sequence = head.last_sequence + 1
            data = trace.to_dict()
            basis = self._basis(
                schema="szl.lyte.anatomy-trace/v1",
                scope=scope,
                stream_kind=STREAM_ANATOMY,
                sequence=sequence,
                previous_hash=head.last_hash,
                created_at=at,
                data=data,
            )
            row = AnatomyTraceRecord(
                id=str(uuid4()),
                tenant_id=str(scope.tenant_id),
                workspace_id=str(scope.workspace_id),
                sequence=sequence,
                previous_hash=head.last_hash,
                record_hash=sha256_json(basis),
                basis_json=basis,
                created_at=at,
                trace_id=str(trace.trace_id),
                span_id=str(trace.span_id),
                parent_span_id=str(trace.parent_span_id) if trace.parent_span_id else None,
                organ=trace.organ.value,
                operation=trace.operation,
                state=trace.state.value,
                truth_label=trace.truth_label.value,
                started_at=trace.started_at,
                completed_at=trace.completed_at,
                input_sha256=trace.input_sha256,
                output_sha256=trace.output_sha256,
                attributes_json=dict(trace.attributes),
            )
            session.add(row)
            self._advance_head(head, sequence, row.record_hash, at)
            session.flush()
            return row

    def insert_operational(
        self,
        scope: Scope,
        draft: OperationalRecordDraft,
        *,
        created_at: datetime | None = None,
    ) -> OperationalRecord:
        """Insert the first immutable version; reject an existing scoped ID."""
        return self._write_operational(
            scope,
            draft,
            insert_only=True,
            expected_version=0,
            created_at=created_at,
        )

    def upsert_operational(
        self,
        scope: Scope,
        draft: OperationalRecordDraft,
        *,
        expected_version: int | None = None,
        created_at: datetime | None = None,
    ) -> OperationalRecord:
        """Append a new entity version, optionally using optimistic concurrency."""
        if expected_version is not None and (
            isinstance(expected_version, bool)
            or not isinstance(expected_version, int)
            or expected_version < 0
        ):
            raise ValueError("expected_version must be a non-negative integer")
        return self._write_operational(
            scope,
            draft,
            insert_only=False,
            expected_version=expected_version,
            created_at=created_at,
        )

    def upsert_operational_batch(
        self,
        scope: Scope,
        drafts: Iterable[OperationalRecordDraft],
        *,
        created_at: datetime | None = None,
    ) -> tuple[OperationalRecord, ...]:
        """Append many independent entity versions in one atomic stream transaction.

        Unchanged records are omitted. This is the deterministic fixture/import path;
        request-time optimistic writes continue to use :meth:`upsert_operational`.
        """

        pending = tuple(drafts)
        identities = tuple((item.entity_kind, item.entity_id) for item in pending)
        if len(set(identities)) != len(identities):
            raise ValueError("operational batch contains duplicate entity identities")
        if not pending:
            return ()
        at = created_at or utc_now()
        written: list[OperationalRecord] = []
        try:
            with self.database.session() as session, session.begin():
                self._assert_scope(session, scope)
                head = self._locked_head(session, scope, STREAM_OPERATIONAL)
                for draft in pending:
                    latest = session.scalar(
                        select(OperationalRecord)
                        .where(
                            OperationalRecord.tenant_id == str(scope.tenant_id),
                            OperationalRecord.workspace_id == str(scope.workspace_id),
                            OperationalRecord.entity_kind == draft.entity_kind.value,
                            OperationalRecord.entity_id == draft.entity_id,
                        )
                        .order_by(OperationalRecord.version.desc())
                        .limit(1)
                    )
                    if latest is not None and (
                        latest.body_json == draft.body
                        and latest.truth_label == draft.truth_label.value
                        and latest.source_revision == draft.source_revision
                    ):
                        continue
                    version = (latest.version if latest else 0) + 1
                    sequence = head.last_sequence + 1
                    data = {
                        "entity_kind": draft.entity_kind.value,
                        "entity_id": draft.entity_id,
                        "version": version,
                        "name": draft.name,
                        "truth_label": draft.truth_label.value,
                        "body": dict(draft.body),
                        "evidence_refs": list(draft.evidence_refs),
                        "source_revision": draft.source_revision,
                        "observed_at": (
                            isoformat_z(draft.observed_at) if draft.observed_at else None
                        ),
                        "metadata": dict(draft.metadata),
                    }
                    basis = self._basis(
                        schema="szl.lyte.operational-record/v1",
                        scope=scope,
                        stream_kind=STREAM_OPERATIONAL,
                        sequence=sequence,
                        previous_hash=head.last_hash,
                        created_at=at,
                        data=data,
                    )
                    row = OperationalRecord(
                        id=str(uuid4()),
                        tenant_id=str(scope.tenant_id),
                        workspace_id=str(scope.workspace_id),
                        sequence=sequence,
                        previous_hash=head.last_hash,
                        record_hash=sha256_json(basis),
                        basis_json=basis,
                        created_at=at,
                        entity_kind=draft.entity_kind.value,
                        entity_id=draft.entity_id,
                        version=version,
                        name=draft.name,
                        truth_label=draft.truth_label.value,
                        body_json=dict(draft.body),
                        evidence_refs=list(draft.evidence_refs),
                        source_revision=draft.source_revision,
                        observed_at=draft.observed_at,
                        metadata_json=dict(draft.metadata),
                    )
                    session.add(row)
                    self._advance_head(head, sequence, row.record_hash, at)
                    written.append(row)
                session.flush()
            return tuple(written)
        except IntegrityError as exc:
            raise OperationalConflict(
                "operational batch append conflicted with concurrent state"
            ) from exc

    def _write_operational(
        self,
        scope: Scope,
        draft: OperationalRecordDraft,
        *,
        insert_only: bool,
        expected_version: int | None,
        created_at: datetime | None,
    ) -> OperationalRecord:
        at = created_at or utc_now()
        try:
            with self.database.session() as session, session.begin():
                self._assert_scope(session, scope)
                latest = session.scalar(
                    select(OperationalRecord)
                    .where(
                        OperationalRecord.tenant_id == str(scope.tenant_id),
                        OperationalRecord.workspace_id == str(scope.workspace_id),
                        OperationalRecord.entity_kind == draft.entity_kind.value,
                        OperationalRecord.entity_id == draft.entity_id,
                    )
                    .order_by(OperationalRecord.version.desc())
                    .limit(1)
                    .with_for_update()
                )
                current_version = latest.version if latest else 0
                if insert_only and latest is not None:
                    raise OperationalConflict("operational entity already exists in this workspace")
                if expected_version is not None and expected_version != current_version:
                    raise OperationalConflict(
                        f"expected version {expected_version}, current version is {current_version}"
                    )
                version = current_version + 1
                head = self._locked_head(session, scope, STREAM_OPERATIONAL)
                sequence = head.last_sequence + 1
                data = {
                    "entity_kind": draft.entity_kind.value,
                    "entity_id": draft.entity_id,
                    "version": version,
                    "name": draft.name,
                    "truth_label": draft.truth_label.value,
                    "body": dict(draft.body),
                    "evidence_refs": list(draft.evidence_refs),
                    "source_revision": draft.source_revision,
                    "observed_at": (isoformat_z(draft.observed_at) if draft.observed_at else None),
                    "metadata": dict(draft.metadata),
                }
                basis = self._basis(
                    schema="szl.lyte.operational-record/v1",
                    scope=scope,
                    stream_kind=STREAM_OPERATIONAL,
                    sequence=sequence,
                    previous_hash=head.last_hash,
                    created_at=at,
                    data=data,
                )
                row = OperationalRecord(
                    id=str(uuid4()),
                    tenant_id=str(scope.tenant_id),
                    workspace_id=str(scope.workspace_id),
                    sequence=sequence,
                    previous_hash=head.last_hash,
                    record_hash=sha256_json(basis),
                    basis_json=basis,
                    created_at=at,
                    entity_kind=draft.entity_kind.value,
                    entity_id=draft.entity_id,
                    version=version,
                    name=draft.name,
                    truth_label=draft.truth_label.value,
                    body_json=dict(draft.body),
                    evidence_refs=list(draft.evidence_refs),
                    source_revision=draft.source_revision,
                    observed_at=draft.observed_at,
                    metadata_json=dict(draft.metadata),
                )
                session.add(row)
                self._advance_head(head, sequence, row.record_hash, at)
                session.flush()
                return row
        except IntegrityError as exc:
            raise OperationalConflict(
                "operational append conflicted with concurrent state"
            ) from exc

    def get_operational(
        self,
        scope: Scope,
        entity_kind: OperationalEntityKind | str,
        entity_id: str,
        *,
        version: int | None = None,
    ) -> OperationalRecord | None:
        """Read one entity strictly within the supplied tenant/workspace scope."""
        kind = OperationalEntityKind(entity_kind).value
        if not isinstance(entity_id, str) or not entity_id.strip():
            raise ValueError("entity_id is required")
        with self.database.session() as session:
            self._assert_scope(session, scope)
            statement = select(OperationalRecord).where(
                OperationalRecord.tenant_id == str(scope.tenant_id),
                OperationalRecord.workspace_id == str(scope.workspace_id),
                OperationalRecord.entity_kind == kind,
                OperationalRecord.entity_id == entity_id,
            )
            if version is None:
                statement = statement.order_by(OperationalRecord.version.desc()).limit(1)
            else:
                if isinstance(version, bool) or not isinstance(version, int) or version < 1:
                    raise ValueError("version must be a positive integer")
                statement = statement.where(OperationalRecord.version == version)
            return session.scalar(statement)

    def list_operational(
        self,
        scope: Scope,
        *,
        entity_kind: OperationalEntityKind | str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[OperationalRecord]:
        """List only latest entity versions inside one mandatory scope."""
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 100_000:
            raise ValueError("offset must be between 0 and 100000")
        kind = OperationalEntityKind(entity_kind).value if entity_kind else None
        with self.database.session() as session:
            self._assert_scope(session, scope)
            latest_query = (
                select(
                    OperationalRecord.entity_kind.label("entity_kind"),
                    OperationalRecord.entity_id.label("entity_id"),
                    func.max(OperationalRecord.version).label("version"),
                )
                .where(
                    OperationalRecord.tenant_id == str(scope.tenant_id),
                    OperationalRecord.workspace_id == str(scope.workspace_id),
                )
                .group_by(OperationalRecord.entity_kind, OperationalRecord.entity_id)
            )
            if kind is not None:
                latest_query = latest_query.where(OperationalRecord.entity_kind == kind)
            latest = latest_query.subquery()
            statement = (
                select(OperationalRecord)
                .join(
                    latest,
                    and_(
                        OperationalRecord.entity_kind == latest.c.entity_kind,
                        OperationalRecord.entity_id == latest.c.entity_id,
                        OperationalRecord.version == latest.c.version,
                    ),
                )
                .where(
                    OperationalRecord.tenant_id == str(scope.tenant_id),
                    OperationalRecord.workspace_id == str(scope.workspace_id),
                )
                .order_by(OperationalRecord.entity_kind, OperationalRecord.entity_id)
                .offset(offset)
                .limit(limit)
            )
            return list(session.scalars(statement))

    def verify_chain(self, scope: Scope, stream_kind: str) -> ChainVerification:
        model_by_stream = {
            STREAM_RECEIPTS: ReceiptRecord,
            STREAM_IDEMPOTENCY: IdempotencyRecord,
            STREAM_MEMORY: MemoryRecord,
            STREAM_ANATOMY: AnatomyTraceRecord,
            STREAM_OPERATIONAL: OperationalRecord,
        }
        model = model_by_stream.get(stream_kind)
        if model is None:
            raise ValueError(f"unknown stream kind: {stream_kind}")
        with self.database.session() as session:
            self._assert_scope(session, scope)
            records = list(
                session.scalars(
                    select(model)
                    .where(
                        model.tenant_id == str(scope.tenant_id),
                        model.workspace_id == str(scope.workspace_id),
                    )
                    .order_by(model.sequence)
                )
            )
            expected_previous: str | None = None
            for expected_sequence, row in enumerate(records, start=1):
                if row.sequence != expected_sequence:
                    return ChainVerification(
                        stream_kind,
                        False,
                        len(records),
                        expected_previous,
                        f"sequence gap at {expected_sequence}",
                    )
                if row.previous_hash != expected_previous:
                    return ChainVerification(
                        stream_kind,
                        False,
                        len(records),
                        expected_previous,
                        f"previous hash mismatch at {expected_sequence}",
                    )
                basis = row.basis_json
                if (
                    basis.get("tenant_id") != str(scope.tenant_id)
                    or basis.get("workspace_id") != str(scope.workspace_id)
                    or basis.get("stream_kind") != stream_kind
                    or basis.get("sequence") != expected_sequence
                    or basis.get("previous_hash") != expected_previous
                ):
                    return ChainVerification(
                        stream_kind,
                        False,
                        len(records),
                        expected_previous,
                        f"basis mismatch at {expected_sequence}",
                    )
                if sha256_json(basis) != row.record_hash:
                    return ChainVerification(
                        stream_kind,
                        False,
                        len(records),
                        expected_previous,
                        f"record digest mismatch at {expected_sequence}",
                    )
                expected_previous = row.record_hash
            head = self._locked_head(session, scope, stream_kind)
            if head.last_sequence != len(records) or head.last_hash != expected_previous:
                return ChainVerification(
                    stream_kind,
                    False,
                    len(records),
                    expected_previous,
                    "stream head does not match append-only records",
                )
            return ChainVerification(stream_kind, True, len(records), expected_previous, None)
