"""Authenticated bounded ingest and allowlisted public-source observation."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from lyte.api.dependencies import get_mutation_scope, get_runtime
from lyte.connectors import (
    ConnectorPolicyError,
    GitHubActionsConnector,
    GitHubActionsResult,
    GovernedEventError,
    GovernedEventVerifier,
    OtlpJsonIngestor,
    PayloadValidationError,
    ReplayDetectedError,
    ReplayProtectionUnavailable,
    SignatureVerificationError,
    TimestampSkewError,
    WebhookIdempotencyConflict,
)
from lyte.domain import (
    OperationalEntityKind,
    OperationalRecordDraft,
    Scope,
    sha256_json,
)
from lyte.persistence import OperationalConflict

router = APIRouter(prefix="/api/lyte/v2", tags=["sources"])
MutationScope = Annotated[Scope, Depends(get_mutation_scope)]

_GITHUB_REPOSITORIES = frozenset(
    {
        "szl-holdings/a11oy",
        "szl-holdings/anatomy",
        "szl-holdings/lyte-lattice",
        "szl-holdings/lyte-services",
        "szl-holdings/szl-formulas",
        "szl-holdings/szl-second-brain",
        "szl-holdings/vertical-services",
    }
)
_GOVERNED_SOURCES = frozenset(
    {
        "generic-webhook",
        "jira",
        "lyte-test-source",
        "salesforce",
        "servicenow",
    }
)


async def _bounded_body(request: Request, maximum: int) -> bytes:
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > maximum:
            raise HTTPException(status_code=413, detail=f"request body exceeds {maximum} bytes")
    if not body:
        raise HTTPException(status_code=400, detail="request body is required")
    return bytes(body)


def _require_idempotency(value: str | None) -> str:
    if value is None or not 16 <= len(value) <= 128 or value.isspace():
        raise HTTPException(
            status_code=400,
            detail="Idempotency-Key must contain 16-128 characters",
        )
    return value


def _persist_projection(
    runtime: Any,
    scope: Scope,
    draft: OperationalRecordDraft,
) -> Any:
    current = runtime.store.get_operational(scope, draft.entity_kind, draft.entity_id)
    if current is not None and current.body_json == draft.body:
        return current
    try:
        return runtime.store.upsert_operational(
            scope,
            draft,
            expected_version=current.version if current else 0,
        )
    except OperationalConflict:
        raced = runtime.store.get_operational(scope, draft.entity_kind, draft.entity_id)
        if raced is None or raced.body_json != draft.body:
            raise
        return raced


@router.post("/ingest/otlp")
async def ingest_otlp(
    request: Request,
    scope: MutationScope,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    key = _require_idempotency(idempotency_key)
    body = await _bounded_body(request, 1_000_000)
    runtime = get_runtime(request)
    try:
        batch = OtlpJsonIngestor().ingest(body, scope=scope, idempotency_key=key)
    except PayloadValidationError as exc:
        runtime.metrics.record_ingest("otlp", "rejected", record_count=1)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    receipt = runtime.store.append_receipt(scope, batch.receipt, idempotency_key=key)
    runtime.metrics.record_receipt(batch.receipt.kind)
    projection = _persist_projection(
        runtime,
        scope,
        OperationalRecordDraft(
            entity_kind=OperationalEntityKind.TELEMETRY_WINDOW,
            entity_id=f"otlp:{batch.idempotency.payload_sha256}",
            name=f"OTLP {batch.signal} ingest",
            body=batch.to_dict(),
            truth_label=batch.receipt.truth_label,
            evidence_refs=(receipt.record_hash,),
            metadata={"durable": True, "bounded_subset": "scalar_v1"},
        ),
    )
    runtime.metrics.record_ingest(batch.signal, "success", record_count=batch.record_count)
    return {
        "schema": "szl.lyte.otlp-ingest/v1",
        **batch.to_dict(),
        "receipt_id": receipt.record_hash,
        "receipt_sequence": receipt.sequence,
        "projection_id": projection.id,
        "durable": True,
        "effectors_enabled": False,
    }


@router.post("/ingest/event")
async def ingest_event(
    request: Request,
    scope: MutationScope,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    signature: str | None = Header(default=None, alias="X-Lyte-Signature"),
    timestamp: str | None = Header(default=None, alias="X-Lyte-Timestamp"),
    nonce: str | None = Header(default=None, alias="X-Lyte-Nonce"),
) -> dict[str, Any]:
    key = _require_idempotency(idempotency_key)
    if timestamp is None or nonce is None:
        raise HTTPException(
            status_code=400,
            detail="X-Lyte-Timestamp and X-Lyte-Nonce are required",
        )
    body = await _bounded_body(request, 256_000)
    runtime = get_runtime(request)
    if runtime.settings.webhook_hmac_secret is None:
        raise HTTPException(
            status_code=503,
            detail="governed-event HMAC verification is not configured; request denied",
        )
    verifier = GovernedEventVerifier(
        allowed_sources=_GOVERNED_SOURCES,
        secret=runtime.settings.webhook_hmac_secret.encode("utf-8"),
        replay_protector=runtime.replay_protector,
    )
    try:
        verified = verifier.verify(
            body,
            scope=scope,
            signature=signature,
            timestamp=timestamp,
            nonce=nonce,
            idempotency_key=key,
        )
    except (SignatureVerificationError, TimestampSkewError) as exc:
        runtime.metrics.record_ingest("event", "rejected", record_count=1)
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except (ReplayDetectedError, WebhookIdempotencyConflict) as exc:
        runtime.metrics.record_ingest("event", "replay", record_count=1)
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ReplayProtectionUnavailable as exc:
        runtime.metrics.record_ingest("event", "unavailable", record_count=1)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (GovernedEventError, PayloadValidationError) as exc:
        runtime.metrics.record_ingest("event", "rejected", record_count=1)
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    receipt = runtime.store.append_receipt(scope, verified.receipt, idempotency_key=key)
    runtime.metrics.record_receipt(verified.receipt.kind)
    projection = _persist_projection(
        runtime,
        scope,
        OperationalRecordDraft(
            entity_kind=OperationalEntityKind.SIGNAL,
            entity_id=verified.event.event_id,
            name=verified.event.event_type,
            body=verified.to_dict(),
            truth_label=verified.event.truth_label,
            evidence_refs=tuple(verified.event.evidence_refs) or (receipt.record_hash,),
            observed_at=verified.event.occurred_at,
            metadata={"durable": True, "signature_verified": True},
        ),
    )
    runtime.metrics.record_ingest("event", "success", record_count=1)
    return {
        "schema": "szl.lyte.governed-event-ingest/v1",
        **verified.to_dict(),
        "receipt_id": receipt.record_hash,
        "receipt_sequence": receipt.sequence,
        "projection_id": projection.id,
        "durable": True,
        "effectors_enabled": False,
    }


@router.get("/github/{repository:path}")
def github_actions(
    repository: str,
    request: Request,
    etag: str | None = Header(default=None, alias="If-None-Match"),
) -> dict[str, Any]:
    """Observe one allowlisted repository without mutating durable state."""

    runtime = get_runtime(request)
    result = _fetch_github(runtime, repository, etag=etag)
    return {
        "schema": "szl.lyte.github-actions/v1",
        **result.to_dict(),
        "receipt_id": None,
        "receipt_persisted": False,
        "durable": False,
        "persistence_requires_authenticated_ingest": True,
        "arbitrary_url_fetch": False,
        "read_only": True,
    }


def _fetch_github(runtime: Any, repository: str, *, etag: str | None) -> GitHubActionsResult:
    try:
        with runtime.metrics.track_connector("github_actions") as observation:
            with GitHubActionsConnector(_GITHUB_REPOSITORIES) as connector:
                result = connector.fetch_workflow_runs(repository, etag=etag)
            if result.state.value == "UNAVAILABLE":
                observation.mark("unavailable")
    except ConnectorPolicyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return result


@router.post("/ingest/github/{repository:path}")
def ingest_github_actions(
    repository: str,
    request: Request,
    scope: MutationScope,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    etag: str | None = Header(default=None, alias="If-None-Match"),
) -> dict[str, Any]:
    """Persist an allowlisted GitHub observation under verified workspace authority."""

    key = _require_idempotency(idempotency_key)
    runtime = get_runtime(request)
    result = _fetch_github(runtime, repository, etag=etag)
    response = result.to_dict()
    receipt = runtime.store.append_receipt(scope, result.receipt, idempotency_key=key)
    runtime.metrics.record_receipt(result.receipt.kind)
    projection = _persist_projection(
        runtime,
        scope,
        OperationalRecordDraft(
            entity_kind=OperationalEntityKind.SOURCE,
            entity_id=f"github:{result.repository}",
            name=f"GitHub Actions {result.repository}",
            body=response,
            truth_label=result.truth_label,
            evidence_refs=(receipt.record_hash,),
            metadata={
                "durable": True,
                "read_only_source": True,
                "arbitrary_url_fetch": False,
                "response_sha256": sha256_json(response),
            },
        ),
    )
    return {
        "schema": "szl.lyte.github-actions/v1",
        **response,
        "receipt_id": receipt.record_hash,
        "receipt_sequence": receipt.sequence,
        "receipt_persisted": True,
        "projection_id": projection.id,
        "durable": True,
        "arbitrary_url_fetch": False,
        "read_only": True,
    }


__all__ = ["router"]
