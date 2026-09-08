"""Deterministic Ask Lyte and tenant-scoped Second Brain routes."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request

from lyte.api.dependencies import get_read_scope, get_runtime
from lyte.api.models import AskRequest
from lyte.api.responses import timestamp
from lyte.demo import sample_memory_digest
from lyte.domain import Scope
from lyte.governance import digest_scope_token
from lyte.intelligence import ask_lyte

router = APIRouter(prefix="/api/lyte/v2", tags=["answer-engine"])
ReadScope = Annotated[Scope, Depends(get_read_scope)]


def _unavailable_answer(question: str, reason: str) -> dict[str, Any]:
    return {
        "citations": [],
        "answer": None,
        "question": question,
        "truth_label": "UNAVAILABLE",
        "confidence": 0.0,
        "confidence_basis": "DETERMINISTIC_QUERY_MATCH",
        "evidence_receipt_ids": [],
        "source_entities": [],
        "formula_ids": [],
        "causality_claimed": False,
        "limitations": [reason],
        "recommended_next_review": None,
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
    if not is_sample:
        return _unavailable_answer(
            payload.question,
            "the deterministic enterprise query engine has no verified real-data "
            "projection for this workspace",
        )
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
        "confidence": 1.0 if raw["intent"] != "unsupported" else 0.0,
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


@router.get("/second-brain")
def second_brain(
    request: Request,
    scope: ReadScope,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    scope_token: str | None = Header(default=None, alias="X-Lyte-Session"),
) -> dict[str, Any]:
    runtime = get_runtime(request)
    is_sample = runtime.demo_mode and scope == runtime.demo_scope
    if is_sample and scope_token is None:
        digest = sample_memory_digest(scope)
        data_mode = "SAMPLE"
    else:
        if scope_token is None:
            raise HTTPException(
                status_code=400,
                detail="X-Lyte-Session is required outside the public sample scope",
            )
        try:
            digest = digest_scope_token(scope, scope_token)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        data_mode = "REAL"
    with runtime.metrics.track_query("second_brain"):
        rows = runtime.store.list_memory(scope, scope_digest=digest, limit=limit)
    return {
        "schema": "szl.lyte.second-brain/v1",
        "items": [
            {
                "id": row.id,
                "kind": row.memory_kind,
                "summary": row.summary,
                "truth_label": row.truth_label,
                "evidence_refs": list(row.evidence_refs),
                "subjects": list(row.subjects),
                "metadata": dict(row.metadata_json),
                "receipt_hash": row.receipt_hash,
                "created_at": timestamp(row.created_at),
            }
            for row in rows
        ],
        "count": len(rows),
        "limit": limit,
        "data_mode": data_mode,
        "scope_digest_exposed": False,
        "raw_session_token_recorded": False,
        "approved_knowledge_separated": True,
        "truth_label": "SAMPLE" if data_mode == "SAMPLE" else "REPORTED",
    }


__all__ = ["router"]
