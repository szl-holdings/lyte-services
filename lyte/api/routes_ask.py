"""Deterministic Ask Lyte and durable tenant/workspace Second Brain routes."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status

from lyte.api.dependencies import (
    get_admin_scope,
    get_mutation_scope,
    get_read_scope,
    get_runtime,
)
from lyte.api.models import AskRequest, SecondBrainMemoryRequest
from lyte.api.responses import timestamp
from lyte.domain import Scope, TruthLabel
from lyte.governance import MemoryDraft, MemoryKind, digest_scope_token
from lyte.intelligence import ask_lyte
from lyte.intelligence.enterprise_ask import ask_enterprise
from lyte.persistence import ScopeNotFound

router = APIRouter(prefix="/api/lyte/v2", tags=["answer-engine"])
ReadScope = Annotated[Scope, Depends(get_read_scope)]
MutationScope = Annotated[Scope, Depends(get_mutation_scope)]
AdminScope = Annotated[Scope, Depends(get_admin_scope)]
MemoryKindFilter = Literal["OBSERVATION", "APPROVED_KNOWLEDGE"]


def _sample_answer(payload: AskRequest, request: Request) -> dict[str, Any]:
    runtime = get_runtime(request)
    with runtime.metrics.track_query("ask_lyte") as observation:
        answer = ask_lyte(payload.question)
        if answer.truth_label.value == "UNAVAILABLE":
            observation.mark("unavailable")
    raw = answer.to_dict()
    formula_ids = [
        str(item.get("name")) for item in raw["formulas"] if isinstance(item.get("name"), str)
    ]
    source_entities = sorted(
        {
            citation["source_type"]
            for citation in raw["citations"]
            if isinstance(citation.get("source_type"), str)
        }
    )
    receipt_hash = (runtime.demo_seed or {}).get("receipt_hash")
    evidence_receipt_ids = [receipt_hash] if receipt_hash and raw["citations"] else []
    limitations = [*raw["missing_evidence"]]
    if raw["reason"]:
        limitations.append(raw["reason"])
    return {
        "citations": raw["citations"],
        "answer": raw["answer"],
        "question": raw["question"],
        "intent": raw["intent"],
        "truth_label": raw["truth_label"],
        "confidence": 0.97 if raw["answer"] is not None else 0.0,
        "confidence_basis": "DETERMINISTIC_QUERY_MATCH",
        "evidence_receipt_ids": evidence_receipt_ids,
        "source_entities": source_entities,
        "formula_ids": formula_ids,
        "causality_claimed": False,
        "limitations": limitations,
        "recommended_next_review": raw["recommendation"],
        "evidence_summary": raw["evidence_summary"],
        "can_execute": False,
    }


@router.post("/ask")
def ask(
    payload: AskRequest,
    request: Request,
    scope: ReadScope,
) -> dict[str, Any]:
    runtime = get_runtime(request)
    is_sample = runtime.demo_mode and scope == runtime.demo_scope
    if is_sample:
        return _sample_answer(payload, request)
    try:
        with runtime.metrics.track_query("ask_lyte") as observation:
            answer = ask_enterprise(runtime.store, scope, payload.question)
            if answer.truth_label == TruthLabel.UNAVAILABLE.value:
                observation.mark("unavailable")
    except ScopeNotFound as exc:
        raise HTTPException(status_code=404, detail="workspace scope is unavailable") from exc
    return answer.to_dict()


def _memory_item(row: Any) -> dict[str, Any]:
    dimensions = row.metadata_json.get("subject_dimensions", {})
    metadata = {
        key: value for key, value in row.metadata_json.items() if key != "subject_dimensions"
    }
    return {
        "id": row.id,
        "record_hash": row.record_hash,
        "kind": row.memory_kind,
        "summary": row.summary,
        "truth_label": row.truth_label,
        "evidence_refs": list(row.evidence_refs),
        "subjects": list(row.subjects),
        "subject_dimensions": dict(dimensions) if isinstance(dimensions, dict) else {},
        "metadata": metadata,
        "receipt_hash": row.receipt_hash,
        "created_at": timestamp(row.created_at),
    }


def _append_memory(
    *,
    request: Request,
    scope: Scope,
    payload: SecondBrainMemoryRequest,
    kind: MemoryKind,
) -> dict[str, Any]:
    runtime = get_runtime(request)
    try:
        row = runtime.store.append_memory(
            scope,
            MemoryDraft(
                kind=kind,
                summary=payload.summary,
                truth_label=TruthLabel(payload.truth_label),
                evidence_refs=tuple(payload.evidence_refs),
                subjects=tuple(payload.subjects),
                subject_dimensions={
                    dimension: tuple(values)
                    for dimension, values in payload.subject_dimensions.items()
                },
                metadata=payload.metadata,
            ),
            receipt_hash=payload.receipt_hash,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ScopeNotFound as exc:
        raise HTTPException(status_code=404, detail="workspace scope is unavailable") from exc
    return {
        "schema": "szl.lyte.second-brain-write/v1",
        "item": _memory_item(row),
        "persistence_scope": "TENANT_WORKSPACE",
        "append_only": True,
        "session_token_recorded": False,
        "session_digest_recorded": False,
        "can_execute": False,
    }


@router.post("/second-brain/observations", status_code=status.HTTP_201_CREATED)
def record_observation(
    payload: SecondBrainMemoryRequest,
    request: Request,
    scope: MutationScope,
) -> dict[str, Any]:
    if payload.truth_label in {"ROADMAP", "UNAVAILABLE"}:
        raise HTTPException(
            status_code=422,
            detail="observation memory cannot be ROADMAP or UNAVAILABLE",
        )
    return _append_memory(
        request=request,
        scope=scope,
        payload=payload,
        kind=MemoryKind.OBSERVATION,
    )


@router.post("/second-brain/approved-knowledge", status_code=status.HTTP_201_CREATED)
def record_approved_knowledge(
    payload: SecondBrainMemoryRequest,
    request: Request,
    scope: AdminScope,
) -> dict[str, Any]:
    return _append_memory(
        request=request,
        scope=scope,
        payload=payload,
        kind=MemoryKind.APPROVED_KNOWLEDGE,
    )


@router.get("/second-brain")
def second_brain(
    request: Request,
    scope: ReadScope,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=1_000)] = 0,
    kind: Annotated[MemoryKindFilter | None, Query()] = None,
    service_id: Annotated[str | None, Query(alias="service", min_length=1, max_length=256)] = None,
    journey_id: Annotated[str | None, Query(alias="journey", min_length=1, max_length=256)] = None,
    outcome_id: Annotated[str | None, Query(alias="outcome", min_length=1, max_length=256)] = None,
    incident_id: Annotated[
        str | None, Query(alias="incident", min_length=1, max_length=256)
    ] = None,
    decision_id: Annotated[
        str | None, Query(alias="decision", min_length=1, max_length=256)
    ] = None,
    receipt_hash: Annotated[
        str | None,
        Query(alias="receipt", pattern=r"^[0-9a-f]{64}$"),
    ] = None,
    created_after: Annotated[datetime | None, Query(alias="from")] = None,
    created_before: Annotated[datetime | None, Query(alias="to")] = None,
    scope_token: str | None = Header(default=None, alias="X-Lyte-Session"),
) -> dict[str, Any]:
    runtime = get_runtime(request)
    is_sample = runtime.demo_mode and scope == runtime.demo_scope
    if scope_token is not None:
        # Validate and immediately discard the request-local digest. Durable
        # retrieval below is exclusively tenant/workspace scoped.
        try:
            transient_digest = digest_scope_token(scope, scope_token)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        del transient_digest
    filters = {
        dimension: value
        for dimension, value in {
            "service": service_id,
            "journey": journey_id,
            "outcome": outcome_id,
            "incident": incident_id,
            "decision": decision_id,
        }.items()
        if value is not None
    }
    try:
        with runtime.metrics.track_query("second_brain"):
            result = runtime.store.list_memory(
                scope,
                kind=kind,
                subject_dimensions=filters,
                receipt_hash=receipt_hash,
                created_after=created_after,
                created_before=created_before,
                limit=limit,
                offset=offset,
            )
    except ScopeNotFound as exc:
        raise HTTPException(status_code=404, detail="workspace scope is unavailable") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    items = [_memory_item(row) for row in result.items]
    data_mode = "SAMPLE" if is_sample else "REAL"
    return {
        "schema": "szl.lyte.second-brain/v2",
        "items": items,
        "count": len(items),
        "limit": limit,
        "offset": offset,
        "next_offset": offset + len(items) if result.has_more else None,
        "filters": {
            "kind": kind,
            "subject_dimensions": filters,
            "receipt": receipt_hash,
            "from": created_after.isoformat() if created_after else None,
            "to": created_before.isoformat() if created_before else None,
        },
        "bounded_scan": {
            "scanned": result.scanned,
            "limit": result.scan_limit,
            "truncated": result.scan_truncated,
            "matched": result.matched_in_scan,
        },
        "data_mode": data_mode,
        "persistence_scope": "TENANT_WORKSPACE",
        "append_only": True,
        "scope_digest_exposed": False,
        "raw_session_token_recorded": False,
        "session_digest_recorded": False,
        "session_digest_exposed": False,
        "approved_knowledge_separated": True,
        "truth_label": (
            "SAMPLE" if data_mode == "SAMPLE" else "REPORTED" if items else "UNAVAILABLE"
        ),
    }


__all__ = ["router"]
