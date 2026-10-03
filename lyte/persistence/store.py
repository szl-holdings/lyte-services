"""Tenant-scoped append-only repositories and hash-chain verification."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, TypeVar
from uuid import uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from lyte.domain import (
    AnatomyTrace,
    OperationalEntityKind,
    OperationalRecordDraft,
    ReceiptDraft,
    Scope,
    TenantSpec,
    TruthLabel,
    WorkspaceSpec,
    isoformat_z,
    sha256_json,
    sha256_text,
    utc_now,
)
from lyte.governance.second_brain import (
    SUBJECT_DIMENSIONS,
    MemoryDraft,
    MemoryKind,
    enterprise_memory_partition,
)

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


class ProvisioningConflict(PersistenceError):
    """An existing identity disagrees with an idempotent provisioning request."""


@dataclass(frozen=True, slots=True)
class ChainVerification:
    stream_kind: str
    valid: bool
    record_count: int
    head_hash: str | None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class ScopeProvisioning:
    scope: Scope
    state: str
    tenant_created: bool
    workspace_created: bool
    receipt_hash: str | None = None


@dataclass(frozen=True, slots=True)
class MemoryQueryResult:
    """One bounded page from durable tenant/workspace memory."""

    items: tuple[MemoryRecord, ...]
    matched_in_scan: int
    scanned: int
    scan_limit: int
    scan_truncated: bool
    has_more: bool


@dataclass(frozen=True, slots=True)
class AnalysisBundle:
    """One atomic analysis write and the records that prove it."""

    receipt: ReceiptRecord
    memory: MemoryRecord
    traces: tuple[AnatomyTraceRecord, ...]
    idempotent_replay: bool


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

    @staticmethod
    def _assert_provisioning_identity(
        *,
        tenant: TenantRecord | None,
        workspace: WorkspaceRecord | None,
        tenant_spec: TenantSpec,
        workspace_spec: WorkspaceSpec,
        stream_kinds: frozenset[str],
    ) -> None:
        if tenant is None or workspace is None:
            raise ProvisioningConflict("scope provisioning did not converge")
        if (tenant.slug, tenant.name) != (tenant_spec.slug, tenant_spec.name):
            raise ProvisioningConflict("tenant identity already exists with different metadata")
        if (
            workspace.tenant_id,
            workspace.slug,
            workspace.name,
        ) != (
            str(tenant_spec.id),
            workspace_spec.slug,
            workspace_spec.name,
        ):
            raise ProvisioningConflict("workspace identity already exists with different metadata")
        if stream_kinds != frozenset(STREAM_KINDS):
            raise ProvisioningConflict("workspace stream initialization is incomplete")

    def _read_provisioned_scope(
        self,
        tenant_spec: TenantSpec,
        workspace_spec: WorkspaceSpec,
    ) -> ScopeProvisioning:
        with self.database.session() as session:
            tenant = session.get(TenantRecord, str(tenant_spec.id))
            workspace = session.get(WorkspaceRecord, str(workspace_spec.id))
            stream_kinds = frozenset(
                session.scalars(
                    select(StreamHeadRecord.stream_kind).where(
                        StreamHeadRecord.tenant_id == str(tenant_spec.id),
                        StreamHeadRecord.workspace_id == str(workspace_spec.id),
                    )
                )
            )
            self._assert_provisioning_identity(
                tenant=tenant,
                workspace=workspace,
                tenant_spec=tenant_spec,
                workspace_spec=workspace_spec,
                stream_kinds=stream_kinds,
            )
            receipt_hash = self._provisioning_receipt_hash(
                session, Scope(tenant_spec.id, workspace_spec.id), tenant_spec, workspace_spec
            )
        return ScopeProvisioning(
            scope=Scope(tenant_spec.id, workspace_spec.id),
            state="EXISTING",
            tenant_created=False,
            workspace_created=False,
            receipt_hash=receipt_hash,
        )

    def _provisioning_receipt_hash(
        self,
        session: Session,
        scope: Scope,
        tenant_spec: TenantSpec,
        workspace_spec: WorkspaceSpec,
    ) -> str | None:
        receipts = list(
            session.scalars(
                select(ReceiptRecord)
                .where(
                    ReceiptRecord.tenant_id == str(scope.tenant_id),
                    ReceiptRecord.workspace_id == str(scope.workspace_id),
                    or_(
                        ReceiptRecord.kind == "scope.provisioned",
                        ReceiptRecord.basis_json["data"]["kind"].as_string() == "scope.provisioned",
                    ),
                )
                .limit(2)
            )
        )
        if not receipts:
            return None  # legacy/internal scope creation is not invented audit evidence
        if len(receipts) != 1:
            raise ProvisioningConflict("scope provisioning receipt is ambiguous")
        receipt = receipts[0]
        try:
            self._validate_canonical_projection(
                receipt,
                scope,
                schema="szl.lyte.receipt/v1",
                stream_kind=STREAM_RECEIPTS,
                data={
                    "kind": receipt.kind,
                    "subject_type": receipt.subject_type,
                    "subject_id": receipt.subject_id,
                    "truth_label": receipt.truth_label,
                    "payload": receipt.payload_json,
                    "payload_sha256": receipt.payload_sha256,
                    "evidence_refs": receipt.evidence_refs,
                },
            )
        except PersistenceError as exc:
            raise ProvisioningConflict(
                "scope provisioning receipt integrity does not close"
            ) from exc
        expected_payload = {
            "tenant": {
                "id": str(tenant_spec.id),
                "slug": tenant_spec.slug,
                "name": tenant_spec.name,
            },
            "workspace": {
                "id": str(workspace_spec.id),
                "slug": workspace_spec.slug,
                "name": workspace_spec.name,
            },
            "sample_data_seeded": False,
        }
        if (
            receipt.payload_json != expected_payload
            or receipt.payload_sha256 != sha256_json(expected_payload)
            or receipt.kind != "scope.provisioned"
            or receipt.subject_type != "workspace"
            or receipt.subject_id != str(scope.workspace_id)
            or receipt.truth_label != TruthLabel.MEASURED.value
            or receipt.evidence_refs != []
        ):
            raise ProvisioningConflict("scope provisioning receipt does not bind this identity")
        return receipt.record_hash

    def _append_provisioning_receipt(
        self, session: Session, tenant_spec: TenantSpec, workspace_spec: WorkspaceSpec
    ) -> str:
        scope = Scope(tenant_spec.id, workspace_spec.id)
        at = utc_now()
        head = self._locked_head(session, scope, STREAM_RECEIPTS)
        sequence = head.last_sequence + 1
        payload = {
            "tenant": {
                "id": str(tenant_spec.id), "slug": tenant_spec.slug, "name": tenant_spec.name,
            },
            "workspace": {
                "id": str(workspace_spec.id), "slug": workspace_spec.slug,
                "name": workspace_spec.name,
            },
            "sample_data_seeded": False,
        }
        data = {
            "kind": "scope.provisioned", "subject_type": "workspace",
            "subject_id": str(workspace_spec.id), "truth_label": "MEASURED",
            "payload": payload, "payload_sha256": sha256_json(payload), "evidence_refs": [],
        }
        basis = self._basis(
            schema="szl.lyte.receipt/v1", scope=scope, stream_kind=STREAM_RECEIPTS,
            sequence=sequence, previous_hash=head.last_hash, created_at=at, data=data,
        )
        receipt = ReceiptRecord(
            id=str(uuid4()), tenant_id=str(scope.tenant_id), workspace_id=str(scope.workspace_id),
            sequence=sequence, previous_hash=head.last_hash, record_hash=sha256_json(basis),
            basis_json=basis, created_at=at, payload_json=payload,
            **{key: value for key, value in data.items() if key != "payload"},
        )
        session.add(receipt)
        self._advance_head(head, sequence, receipt.record_hash, at)
        session.flush()
        return receipt.record_hash

    def _provision_scope_once(
        self,
        tenant_spec: TenantSpec,
        workspace_spec: WorkspaceSpec,
    ) -> ScopeProvisioning:
        if workspace_spec.tenant_id != tenant_spec.id:
            raise ProvisioningConflict("workspace tenant does not match the tenant identity")
        tenant_created = False
        workspace_created = False
        try:
            with self.database.session() as session, session.begin():
                tenant = session.get(
                    TenantRecord,
                    str(tenant_spec.id),
                    with_for_update=True,
                )
                if tenant is None:
                    tenant = TenantRecord(
                        id=str(tenant_spec.id),
                        slug=tenant_spec.slug,
                        name=tenant_spec.name,
                        created_at=utc_now(),
                    )
                    session.add(tenant)
                    session.flush()
                    tenant_created = True
                elif (tenant.slug, tenant.name) != (tenant_spec.slug, tenant_spec.name):
                    raise ProvisioningConflict(
                        "tenant identity already exists with different metadata"
                    )

                workspace = session.get(
                    WorkspaceRecord,
                    str(workspace_spec.id),
                    with_for_update=True,
                )
                if workspace is None:
                    workspace = WorkspaceRecord(
                        id=str(workspace_spec.id),
                        tenant_id=str(tenant_spec.id),
                        slug=workspace_spec.slug,
                        name=workspace_spec.name,
                        created_at=utc_now(),
                    )
                    session.add(workspace)
                    session.flush()
                    for stream_kind in STREAM_KINDS:
                        session.add(
                            StreamHeadRecord(
                                tenant_id=str(tenant_spec.id),
                                workspace_id=str(workspace_spec.id),
                                stream_kind=stream_kind,
                                last_sequence=0,
                                last_hash=None,
                                updated_at=utc_now(),
                            )
                        )
                    workspace_created = True
                    session.flush()
                    receipt_hash = self._append_provisioning_receipt(
                        session, tenant_spec, workspace_spec
                    )
                else:
                    stream_kinds = frozenset(
                        session.scalars(
                            select(StreamHeadRecord.stream_kind).where(
                                StreamHeadRecord.tenant_id == str(tenant_spec.id),
                                StreamHeadRecord.workspace_id == str(workspace_spec.id),
                            )
                        )
                    )
                    self._assert_provisioning_identity(
                        tenant=tenant,
                        workspace=workspace,
                        tenant_spec=tenant_spec,
                        workspace_spec=workspace_spec,
                        stream_kinds=stream_kinds,
                    )
                    receipt_hash = self._provisioning_receipt_hash(
                        session,
                        Scope(tenant_spec.id, workspace_spec.id),
                        tenant_spec,
                        workspace_spec,
                    )
        except IntegrityError:
            raise

        return ScopeProvisioning(
            scope=Scope(tenant_spec.id, workspace_spec.id),
            state="CREATED" if tenant_created or workspace_created else "EXISTING",
            tenant_created=tenant_created,
            workspace_created=workspace_created,
            receipt_hash=receipt_hash,
        )

    def _retryable_missing_workspace(
        self,
        tenant_spec: TenantSpec,
        workspace_spec: WorkspaceSpec,
    ) -> bool:
        """Return true only for the benign shared-tenant bootstrap race."""

        with self.database.session() as session:
            tenant = session.get(TenantRecord, str(tenant_spec.id))
            if tenant is None or (tenant.slug, tenant.name) != (
                tenant_spec.slug,
                tenant_spec.name,
            ):
                return False
            workspace = session.get(WorkspaceRecord, str(workspace_spec.id))
            if workspace is not None:
                return False
            slug_owner = session.scalar(
                select(WorkspaceRecord.id).where(
                    WorkspaceRecord.tenant_id == str(tenant_spec.id),
                    WorkspaceRecord.slug == workspace_spec.slug,
                )
            )
            return slug_owner is None

    def provision_scope(
        self,
        tenant_spec: TenantSpec,
        workspace_spec: WorkspaceSpec,
    ) -> ScopeProvisioning:
        """Atomically provision one exact admin-authorized scope.

        Replaying an identical request returns ``EXISTING``. A bounded retry
        admits two valid workspaces racing to create the same exact tenant.
        Reusing a UUID or slug with different metadata, or encountering a
        partially initialized scope, still fails closed.
        """

        for attempt in range(2):
            try:
                return self._provision_scope_once(tenant_spec, workspace_spec)
            except IntegrityError:
                try:
                    return self._read_provisioned_scope(tenant_spec, workspace_spec)
                except ProvisioningConflict:
                    if attempt == 0 and self._retryable_missing_workspace(
                        tenant_spec,
                        workspace_spec,
                    ):
                        continue
                    raise
        raise ProvisioningConflict("scope provisioning did not converge")  # pragma: no cover

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
        receipt_hash: str,
        created_at: datetime | None = None,
    ) -> MemoryRecord:
        at = created_at or utc_now()
        if (
            len(receipt_hash) != 64 or any(ch not in "0123456789abcdef" for ch in receipt_hash)
        ):
            raise ValueError("receipt_hash must be a lowercase SHA-256 digest")
        partition_digest = enterprise_memory_partition(scope)
        metadata = dict(draft.metadata)
        metadata["subject_dimensions"] = {
            dimension: list(values) for dimension, values in draft.subject_dimensions.items()
        }
        with self.database.session() as session, session.begin():
            self._assert_scope(session, scope)
            evidence_receipt = session.scalar(
                select(ReceiptRecord).where(
                    ReceiptRecord.tenant_id == str(scope.tenant_id),
                    ReceiptRecord.workspace_id == str(scope.workspace_id),
                    ReceiptRecord.record_hash == receipt_hash,
                )
            )
            if evidence_receipt is None:
                raise ValueError("memory receipt_hash does not exist in this scope")
            compatible_labels = {
                TruthLabel.MEASURED: {TruthLabel.MEASURED.value},
                TruthLabel.REPORTED: {TruthLabel.MEASURED.value, TruthLabel.REPORTED.value},
                TruthLabel.MODELED: {
                    TruthLabel.MEASURED.value,
                    TruthLabel.REPORTED.value,
                    TruthLabel.MODELED.value,
                },
                TruthLabel.SAMPLE: {TruthLabel.SAMPLE.value},
            }
            if evidence_receipt.truth_label not in compatible_labels.get(
                draft.truth_label, set()
            ):
                raise ValueError("memory truth_label is incompatible with its evidence receipt")
            if receipt_hash not in draft.evidence_refs:
                raise ValueError("memory evidence_refs must include its evidence receipt hash")
            head = self._locked_head(session, scope, STREAM_MEMORY)
            sequence = head.last_sequence + 1
            data = {
                "partition_digest": partition_digest,
                "memory_kind": draft.kind.value,
                "summary": draft.summary,
                "truth_label": draft.truth_label.value,
                "evidence_refs": list(draft.evidence_refs),
                "subjects": list(draft.subjects),
                "metadata": metadata,
                "receipt_hash": receipt_hash,
            }
            basis = self._basis(
                schema="szl.lyte.memory-record/v2",
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
                scope_digest=partition_digest,
                memory_kind=draft.kind.value,
                summary=draft.summary,
                truth_label=draft.truth_label.value,
                evidence_refs=list(draft.evidence_refs),
                subjects=list(draft.subjects),
                metadata_json=metadata,
                receipt_hash=receipt_hash,
            )
            session.add(row)
            self._advance_head(head, sequence, row.record_hash, at)
            session.flush()
            return row

    def list_memory(
        self,
        scope: Scope,
        *,
        kind: MemoryKind | str | None = None,
        subject_dimensions: Mapping[str, str] | None = None,
        receipt_hash: str | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
        scan_limit: int = 1_000,
    ) -> MemoryQueryResult:
        """Retrieve durable memory with an explicit, bounded candidate window.

        Subject dimensions are stored as bounded JSON metadata for portability
        across SQLite and PostgreSQL. At most ``scan_limit + 1`` rows are read;
        callers receive an explicit truncation signal instead of an incomplete
        result disguised as an exhaustive query.
        """

        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 1_000:
            raise ValueError("offset must be between 0 and 1000")
        if (
            isinstance(scan_limit, bool)
            or not isinstance(scan_limit, int)
            or not 1 <= scan_limit <= 1_000
        ):
            raise ValueError("scan_limit must be between 1 and 1000")
        memory_kind = MemoryKind(kind).value if kind is not None else None
        dimensions: dict[str, str] = {}
        for raw_dimension, raw_value in (subject_dimensions or {}).items():
            dimension = str(raw_dimension).strip().lower()
            value = str(raw_value).strip()
            if dimension not in SUBJECT_DIMENSIONS:
                raise ValueError(f"unsupported memory subject dimension: {dimension}")
            if not value:
                raise ValueError(f"memory subject dimension {dimension} cannot be blank")
            dimensions[dimension] = value
        if receipt_hash is not None and (
            len(receipt_hash) != 64
            or any(character not in "0123456789abcdef" for character in receipt_hash)
        ):
            raise ValueError("receipt_hash must be a lowercase SHA-256 digest")
        if created_after is not None and created_after.tzinfo is None:
            raise ValueError("created_after must be timezone-aware")
        if created_before is not None and created_before.tzinfo is None:
            raise ValueError("created_before must be timezone-aware")
        if created_after is not None and created_before is not None:
            if created_before < created_after:
                raise ValueError("created_before cannot precede created_after")
        with self.database.session() as session:
            self._assert_scope(session, scope)
            statement = (
                select(MemoryRecord)
                .where(
                    MemoryRecord.tenant_id == str(scope.tenant_id),
                    MemoryRecord.workspace_id == str(scope.workspace_id),
                    MemoryRecord.scope_digest == enterprise_memory_partition(scope),
                )
                .order_by(MemoryRecord.sequence.desc())
                .limit(scan_limit + 1)
            )
            if memory_kind is not None:
                statement = statement.where(MemoryRecord.memory_kind == memory_kind)
            if receipt_hash is not None:
                statement = statement.where(MemoryRecord.receipt_hash == receipt_hash)
            if created_after is not None:
                statement = statement.where(MemoryRecord.created_at >= created_after)
            if created_before is not None:
                statement = statement.where(MemoryRecord.created_at <= created_before)
            candidates = list(session.scalars(statement))
        scan_truncated = len(candidates) > scan_limit
        scanned_rows = candidates[:scan_limit]

        def matches(row: MemoryRecord) -> bool:
            stored = row.metadata_json.get("subject_dimensions", {})
            if not isinstance(stored, Mapping):
                return not dimensions
            return all(
                isinstance(stored.get(dimension), list)
                and value in stored[dimension]
                for dimension, value in dimensions.items()
            )

        matched = [row for row in scanned_rows if matches(row)]
        page = tuple(matched[offset : offset + limit])
        return MemoryQueryResult(
            items=page,
            matched_in_scan=len(matched),
            scanned=len(scanned_rows),
            scan_limit=scan_limit,
            scan_truncated=scan_truncated,
            has_more=len(matched) > offset + len(page),
        )

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

    def _canonical_timestamp(self, value: datetime) -> str:
        # SQLite drops the timezone from DateTime(timezone=True) on read. All
        # application writes use canonical UTC; PostgreSQL retains the offset.
        try:
            if value.tzinfo is None and self.database.engine.dialect.name == "sqlite":
                value = value.replace(tzinfo=UTC)
            return isoformat_z(value)
        except (AttributeError, TypeError, ValueError) as exc:
            raise PersistenceError("durable record has an invalid timestamp") from exc

    def _validate_canonical_projection(
        self,
        row: AppendRecord,
        scope: Scope,
        *,
        schema: str,
        stream_kind: str,
        data: Mapping[str, Any],
    ) -> None:
        """Bind the fields returned by a replay to their canonical hash basis."""

        try:
            expected = self._basis(
                schema=schema,
                scope=scope,
                stream_kind=stream_kind,
                sequence=row.sequence,
                previous_hash=row.previous_hash,
                created_at=datetime.fromisoformat(self._canonical_timestamp(row.created_at)),
                data=data,
            )
            valid = (
                row.tenant_id == str(scope.tenant_id)
                and row.workspace_id == str(scope.workspace_id)
                and row.basis_json == expected
                and sha256_json(expected) == row.record_hash
            )
        except (AttributeError, TypeError, ValueError) as exc:
            raise PersistenceError(
                "durable record has an invalid canonical basis"
            ) from exc
        if not valid:
            raise PersistenceError("durable record failed canonical projection validation")

    def _resolve_analysis_bundle(
        self,
        session: Session,
        scope: Scope,
        record: IdempotencyRecord,
        request_digest: str,
        *,
        idempotent_replay: bool,
    ) -> AnalysisBundle:
        self._validate_canonical_projection(
            record,
            scope,
            schema="szl.lyte.idempotency-record/v1",
            stream_kind=STREAM_IDEMPOTENCY,
            data={
                "key_digest": record.key_digest,
                "request_digest": record.request_digest,
                "response_receipt_hash": record.response_receipt_hash,
            },
        )
        receipt = self._resolve_idempotent_receipt(session, scope, record, request_digest)
        self._validate_canonical_projection(
            receipt,
            scope,
            schema="szl.lyte.receipt/v1",
            stream_kind=STREAM_RECEIPTS,
            data={
                "kind": receipt.kind,
                "subject_type": receipt.subject_type,
                "subject_id": receipt.subject_id,
                "truth_label": receipt.truth_label,
                "payload": receipt.payload_json,
                "payload_sha256": receipt.payload_sha256,
                "evidence_refs": receipt.evidence_refs,
            },
        )
        if not isinstance(receipt.payload_json, dict):
            raise PersistenceError("analysis receipt has an invalid payload")
        trace_id = receipt.payload_json.get("trace_id")
        if not isinstance(trace_id, str):
            raise PersistenceError("analysis receipt is missing its anatomy trace linkage")
        memory = session.scalar(
            select(MemoryRecord).where(
                MemoryRecord.tenant_id == str(scope.tenant_id),
                MemoryRecord.workspace_id == str(scope.workspace_id),
                MemoryRecord.receipt_hash == receipt.record_hash,
            )
        )
        traces = tuple(
            session.scalars(
                select(AnatomyTraceRecord)
                .where(
                    AnatomyTraceRecord.tenant_id == str(scope.tenant_id),
                    AnatomyTraceRecord.workspace_id == str(scope.workspace_id),
                    AnatomyTraceRecord.trace_id == trace_id,
                )
                .order_by(AnatomyTraceRecord.sequence)
            )
        )
        expected_stage_sequences = tuple(range(1, 10))
        if any(not isinstance(trace.attributes_json, dict) for trace in traces):
            raise PersistenceError("analysis trace has invalid stage attributes")
        stored_stage_sequences = tuple(
            trace.attributes_json.get("sequence") for trace in traces
        )
        links_are_valid = bool(traces) and traces[0].parent_span_id is None
        for previous, current in zip(traces, traces[1:], strict=False):
            links_are_valid = links_are_valid and (
                current.parent_span_id == previous.span_id
                and current.input_sha256 == previous.output_sha256
            )
        if (
            memory is None
            or len(traces) != 9
            or stored_stage_sequences != expected_stage_sequences
            or not links_are_valid
            or not isinstance(memory.metadata_json, dict)
            or memory.metadata_json.get("trace_id") != trace_id
        ):
            raise PersistenceError("analysis receipt references an incomplete durable bundle")
        self._validate_canonical_projection(
            memory,
            scope,
            schema="szl.lyte.memory-record/v2",
            stream_kind=STREAM_MEMORY,
            data={
                "partition_digest": memory.scope_digest,
                "memory_kind": memory.memory_kind,
                "summary": memory.summary,
                "truth_label": memory.truth_label,
                "evidence_refs": memory.evidence_refs,
                "subjects": memory.subjects,
                "metadata": memory.metadata_json,
                "receipt_hash": memory.receipt_hash,
            },
        )
        for trace in traces:
            self._validate_canonical_projection(
                trace,
                scope,
                schema="szl.lyte.anatomy-trace/v1",
                stream_kind=STREAM_ANATOMY,
                data={
                    **scope.to_dict(),
                    "trace_id": trace.trace_id,
                    "span_id": trace.span_id,
                    "parent_span_id": trace.parent_span_id,
                    "organ": trace.organ,
                    "operation": trace.operation,
                    "state": trace.state,
                    "truth_label": trace.truth_label,
                    "started_at": self._canonical_timestamp(trace.started_at),
                    "completed_at": (
                        self._canonical_timestamp(trace.completed_at)
                        if trace.completed_at is not None
                        else None
                    ),
                    "input_sha256": trace.input_sha256,
                    "output_sha256": trace.output_sha256,
                    "attributes": trace.attributes_json,
                },
            )
        anatomy_digest = sha256_json(
            [
                {
                    "sequence": trace.attributes_json.get("sequence"),
                    "stage": trace.attributes_json.get("stage"),
                    "state": trace.attributes_json.get("stage_state"),
                    "input_sha256": trace.input_sha256,
                    "basis_sha256": trace.attributes_json.get("basis_sha256"),
                    "output_sha256": trace.output_sha256,
                }
                for trace in traces
            ]
        )
        if (
            receipt.payload_json.get("anatomy_schema") != "szl.lyte.analysis-anatomy/v1"
            or receipt.payload_json.get("anatomy_sha256") != anatomy_digest
            or traces[0].input_sha256 != receipt.payload_json.get("input_sha256")
            or memory.scope_digest != enterprise_memory_partition(scope)
            or memory.truth_label != receipt.truth_label
            or receipt.record_hash not in memory.evidence_refs
            or memory.metadata_json.get("approved_knowledge") is not False
            or memory.metadata_json.get("analysis_input_sha256")
            != receipt.payload_json.get("input_sha256")
            or memory.metadata_json.get("analysis_output_sha256")
            != receipt.payload_json.get("output_sha256")
            or any(
                trace.truth_label != receipt.truth_label
                or trace.attributes_json.get("source_revision")
                != receipt.payload_json.get("source_revision")
                for trace in traces
            )
        ):
            raise PersistenceError("analysis durable bundle failed receipt linkage validation")
        return AnalysisBundle(receipt, memory, traces, idempotent_replay)

    def append_analysis_bundle(
        self,
        scope: Scope,
        receipt_draft: ReceiptDraft,
        memory_draft: MemoryDraft,
        traces: tuple[AnatomyTrace, ...],
        *,
        idempotency_key: str,
        request_digest: str,
        created_at: datetime | None = None,
    ) -> AnalysisBundle:
        """Atomically append one analysis receipt, memory, trace chain, and claim.

        A successful return proves that all four scoped streams advanced in one
        database transaction. An identical idempotency replay reads the original
        bundle; a different request digest fails before any stream advances.
        """

        if len(request_digest) != 64 or any(
            character not in "0123456789abcdef" for character in request_digest
        ):
            raise ValueError("request_digest must be a lowercase SHA-256 digest")
        if len(traces) != 9:
            raise ValueError("analysis bundle requires exactly nine anatomy traces")
        trace_ids = {trace.trace_id for trace in traces}
        if len(trace_ids) != 1 or any(trace.scope != scope for trace in traces):
            raise ValueError("analysis traces must share one trace and authenticated scope")
        expected_sequences = tuple(range(1, 10))
        observed_sequences = tuple(
            int(trace.attributes.get("sequence", -1)) for trace in traces
        )
        if observed_sequences != expected_sequences:
            raise ValueError("analysis traces must contain the canonical stage sequence")
        trace_id = str(next(iter(trace_ids)))
        if receipt_draft.payload.get("trace_id") != trace_id:
            raise ValueError("analysis receipt must link the persisted trace_id")
        if memory_draft.metadata.get("trace_id") != trace_id:
            raise ValueError("analysis memory must link the persisted trace_id")
        if traces[0].parent_span_id is not None:
            raise ValueError("the first analysis stage cannot have a parent span")
        for previous, current in zip(traces, traces[1:], strict=False):
            if (
                current.parent_span_id != previous.span_id
                or current.input_sha256 != previous.output_sha256
            ):
                raise ValueError("analysis traces must form one digest and parent-span chain")
        if any(
            trace.state.value != "COMPLETED"
            or trace.output_sha256 is None
            or trace.attributes.get("human_approval_required") is not True
            or trace.attributes.get("can_authorize") is not False
            or trace.attributes.get("can_execute") is not False
            or trace.attributes.get("effectors_enabled") is not False
            for trace in traces
        ):
            raise ValueError("analysis traces must preserve the human-sovereign boundary")

        at = created_at or utc_now()
        key_digest = self._idempotency_digest(scope, idempotency_key)
        partition_digest = enterprise_memory_partition(scope)
        memory_metadata = dict(memory_draft.metadata)
        memory_metadata["subject_dimensions"] = {
            dimension: list(values)
            for dimension, values in memory_draft.subject_dimensions.items()
        }
        try:
            with self.database.session() as session, session.begin():
                self._assert_scope(session, scope)
                existing = session.scalar(
                    select(IdempotencyRecord).where(
                        IdempotencyRecord.tenant_id == str(scope.tenant_id),
                        IdempotencyRecord.workspace_id == str(scope.workspace_id),
                        IdempotencyRecord.key_digest == key_digest,
                    )
                )
                if existing is not None:
                    return self._resolve_analysis_bundle(
                        session,
                        scope,
                        existing,
                        request_digest,
                        idempotent_replay=True,
                    )

                receipt_head = self._locked_head(session, scope, STREAM_RECEIPTS)
                idempotency_head = self._locked_head(session, scope, STREAM_IDEMPOTENCY)
                memory_head = self._locked_head(session, scope, STREAM_MEMORY)
                anatomy_head = self._locked_head(session, scope, STREAM_ANATOMY)

                # A competing writer may have committed the same key while
                # this transaction waited for the scoped stream locks.
                existing = session.scalar(
                    select(IdempotencyRecord).where(
                        IdempotencyRecord.tenant_id == str(scope.tenant_id),
                        IdempotencyRecord.workspace_id == str(scope.workspace_id),
                        IdempotencyRecord.key_digest == key_digest,
                    )
                )
                if existing is not None:
                    return self._resolve_analysis_bundle(
                        session,
                        scope,
                        existing,
                        request_digest,
                        idempotent_replay=True,
                    )

                receipt_sequence = receipt_head.last_sequence + 1
                receipt_data = {
                    "kind": receipt_draft.kind,
                    "subject_type": receipt_draft.subject_type,
                    "subject_id": receipt_draft.subject_id,
                    "truth_label": receipt_draft.truth_label.value,
                    "payload": dict(receipt_draft.payload),
                    "payload_sha256": sha256_json(receipt_draft.payload),
                    "evidence_refs": list(receipt_draft.evidence_refs),
                }
                receipt_basis = self._basis(
                    schema="szl.lyte.receipt/v1",
                    scope=scope,
                    stream_kind=STREAM_RECEIPTS,
                    sequence=receipt_sequence,
                    previous_hash=receipt_head.last_hash,
                    created_at=at,
                    data=receipt_data,
                )
                receipt = ReceiptRecord(
                    id=str(uuid4()),
                    tenant_id=str(scope.tenant_id),
                    workspace_id=str(scope.workspace_id),
                    sequence=receipt_sequence,
                    previous_hash=receipt_head.last_hash,
                    record_hash=sha256_json(receipt_basis),
                    basis_json=receipt_basis,
                    created_at=at,
                    kind=receipt_draft.kind,
                    subject_type=receipt_draft.subject_type,
                    subject_id=receipt_draft.subject_id,
                    truth_label=receipt_draft.truth_label.value,
                    payload_json=dict(receipt_draft.payload),
                    payload_sha256=receipt_data["payload_sha256"],
                    evidence_refs=list(receipt_draft.evidence_refs),
                )
                session.add(receipt)
                self._advance_head(receipt_head, receipt_sequence, receipt.record_hash, at)

                idempotency_sequence = idempotency_head.last_sequence + 1
                idempotency_data = {
                    "key_digest": key_digest,
                    "request_digest": request_digest,
                    "response_receipt_hash": receipt.record_hash,
                }
                idempotency_basis = self._basis(
                    schema="szl.lyte.idempotency-record/v1",
                    scope=scope,
                    stream_kind=STREAM_IDEMPOTENCY,
                    sequence=idempotency_sequence,
                    previous_hash=idempotency_head.last_hash,
                    created_at=at,
                    data=idempotency_data,
                )
                idempotency = IdempotencyRecord(
                    id=str(uuid4()),
                    tenant_id=str(scope.tenant_id),
                    workspace_id=str(scope.workspace_id),
                    sequence=idempotency_sequence,
                    previous_hash=idempotency_head.last_hash,
                    record_hash=sha256_json(idempotency_basis),
                    basis_json=idempotency_basis,
                    created_at=at,
                    **idempotency_data,
                )
                session.add(idempotency)
                self._advance_head(
                    idempotency_head,
                    idempotency_sequence,
                    idempotency.record_hash,
                    at,
                )

                memory_sequence = memory_head.last_sequence + 1
                memory_evidence_refs = list(
                    dict.fromkeys((*memory_draft.evidence_refs, receipt.record_hash))
                )
                memory_data = {
                    "partition_digest": partition_digest,
                    "memory_kind": memory_draft.kind.value,
                    "summary": memory_draft.summary,
                    "truth_label": memory_draft.truth_label.value,
                    "evidence_refs": memory_evidence_refs,
                    "subjects": list(memory_draft.subjects),
                    "metadata": memory_metadata,
                    "receipt_hash": receipt.record_hash,
                }
                memory_basis = self._basis(
                    schema="szl.lyte.memory-record/v2",
                    scope=scope,
                    stream_kind=STREAM_MEMORY,
                    sequence=memory_sequence,
                    previous_hash=memory_head.last_hash,
                    created_at=at,
                    data=memory_data,
                )
                memory = MemoryRecord(
                    id=str(uuid4()),
                    tenant_id=str(scope.tenant_id),
                    workspace_id=str(scope.workspace_id),
                    sequence=memory_sequence,
                    previous_hash=memory_head.last_hash,
                    record_hash=sha256_json(memory_basis),
                    basis_json=memory_basis,
                    created_at=at,
                    scope_digest=partition_digest,
                    memory_kind=memory_draft.kind.value,
                    summary=memory_draft.summary,
                    truth_label=memory_draft.truth_label.value,
                    evidence_refs=memory_evidence_refs,
                    subjects=list(memory_draft.subjects),
                    metadata_json=memory_metadata,
                    receipt_hash=receipt.record_hash,
                )
                session.add(memory)
                self._advance_head(memory_head, memory_sequence, memory.record_hash, at)

                trace_rows: list[AnatomyTraceRecord] = []
                previous_hash = anatomy_head.last_hash
                first_sequence = anatomy_head.last_sequence + 1
                for offset, trace in enumerate(traces):
                    sequence = first_sequence + offset
                    trace_data = trace.to_dict()
                    trace_basis = self._basis(
                        schema="szl.lyte.anatomy-trace/v1",
                        scope=scope,
                        stream_kind=STREAM_ANATOMY,
                        sequence=sequence,
                        previous_hash=previous_hash,
                        created_at=at,
                        data=trace_data,
                    )
                    trace_row = AnatomyTraceRecord(
                        id=str(uuid4()),
                        tenant_id=str(scope.tenant_id),
                        workspace_id=str(scope.workspace_id),
                        sequence=sequence,
                        previous_hash=previous_hash,
                        record_hash=sha256_json(trace_basis),
                        basis_json=trace_basis,
                        created_at=at,
                        trace_id=str(trace.trace_id),
                        span_id=str(trace.span_id),
                        parent_span_id=(
                            str(trace.parent_span_id) if trace.parent_span_id else None
                        ),
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
                    session.add(trace_row)
                    trace_rows.append(trace_row)
                    previous_hash = trace_row.record_hash
                self._advance_head(
                    anatomy_head,
                    trace_rows[-1].sequence,
                    trace_rows[-1].record_hash,
                    at,
                )
                session.flush()
                return self._resolve_analysis_bundle(
                    session,
                    scope,
                    idempotency,
                    request_digest,
                    idempotent_replay=False,
                )
        except IntegrityError as exc:
            try:
                with self.database.session() as session:
                    existing = session.scalar(
                        select(IdempotencyRecord).where(
                            IdempotencyRecord.tenant_id == str(scope.tenant_id),
                            IdempotencyRecord.workspace_id == str(scope.workspace_id),
                            IdempotencyRecord.key_digest == key_digest,
                        )
                    )
                    if existing is not None:
                        return self._resolve_analysis_bundle(
                            session,
                            scope,
                            existing,
                            request_digest,
                            idempotent_replay=True,
                        )
            except SQLAlchemyError as recovery_exc:
                raise PersistenceError("analysis bundle recovery is unavailable") from recovery_exc
            raise PersistenceError(
                "analysis bundle append conflicted with concurrent state"
            ) from exc
        except SQLAlchemyError as exc:
            raise PersistenceError("analysis durable bundle database operation failed") from exc

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
