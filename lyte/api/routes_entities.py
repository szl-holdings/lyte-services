"""Tenant-scoped operational views for Lyte's six lenses."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from lyte.api.dependencies import get_read_scope, get_runtime
from lyte.api.responses import operational_page, operational_record, receipt_record
from lyte.domain import OperationalEntityKind, Scope

router = APIRouter(prefix="/api/lyte/v2", tags=["intelligence"])

Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=100_000)]
ReadScope = Annotated[Scope, Depends(get_read_scope)]


def _data_mode(runtime: Any, scope: Scope) -> str:
    return "SAMPLE" if runtime.demo_mode and scope == runtime.demo_scope else "REAL"


def _list(
    request: Request,
    scope: Scope,
    kind: OperationalEntityKind,
    *,
    limit: int,
    offset: int,
    metric: str,
) -> dict[str, Any]:
    runtime = get_runtime(request)
    with runtime.metrics.track_query(metric):
        rows = runtime.store.list_operational(
            scope,
            entity_kind=kind,
            limit=limit,
            offset=offset,
        )
    return operational_page(
        rows,
        limit=limit,
        offset=offset,
        data_mode=_data_mode(runtime, scope),
    )


def _get(
    request: Request,
    scope: Scope,
    kind: OperationalEntityKind,
    entity_id: str,
    *,
    metric: str,
) -> dict[str, Any]:
    runtime = get_runtime(request)
    with runtime.metrics.track_query(metric) as observation:
        row = runtime.store.get_operational(scope, kind, entity_id)
        if row is None:
            observation.mark("unavailable")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"{kind.value} was not found in this workspace",
            )
    return operational_record(row)


@router.get("/services")
def services(
    request: Request,
    scope: ReadScope,
    limit: Limit = 50,
    offset: Offset = 0,
) -> dict[str, Any]:
    return _list(
        request,
        scope,
        OperationalEntityKind.SERVICE,
        limit=limit,
        offset=offset,
        metric="services",
    )


@router.get("/services/{service_id}")
def service(
    service_id: str,
    request: Request,
    scope: ReadScope,
) -> dict[str, Any]:
    return _get(
        request,
        scope,
        OperationalEntityKind.SERVICE,
        service_id,
        metric="service_detail",
    )


@router.get("/journeys")
def journeys(
    request: Request,
    scope: ReadScope,
    limit: Limit = 50,
    offset: Offset = 0,
) -> dict[str, Any]:
    return _list(
        request,
        scope,
        OperationalEntityKind.CUSTOMER_JOURNEY,
        limit=limit,
        offset=offset,
        metric="journeys",
    )


@router.get("/journeys/{journey_id}")
def journey(
    journey_id: str,
    request: Request,
    scope: ReadScope,
) -> dict[str, Any]:
    return _get(
        request,
        scope,
        OperationalEntityKind.CUSTOMER_JOURNEY,
        journey_id,
        metric="journey_detail",
    )


@router.get("/outcomes")
def outcomes(
    request: Request,
    scope: ReadScope,
    limit: Limit = 50,
    offset: Offset = 0,
) -> dict[str, Any]:
    return _list(
        request,
        scope,
        OperationalEntityKind.BUSINESS_OUTCOME,
        limit=limit,
        offset=offset,
        metric="outcomes",
    )


@router.get("/agents")
def agents(
    request: Request,
    scope: ReadScope,
    limit: Limit = 50,
    offset: Offset = 0,
) -> dict[str, Any]:
    return _list(
        request,
        scope,
        OperationalEntityKind.AGENT_TRACE_SUMMARY,
        limit=limit,
        offset=offset,
        metric="agents",
    )


@router.get("/incidents")
def incidents(
    request: Request,
    scope: ReadScope,
    limit: Limit = 50,
    offset: Offset = 0,
) -> dict[str, Any]:
    return _list(
        request,
        scope,
        OperationalEntityKind.INCIDENT,
        limit=limit,
        offset=offset,
        metric="incidents",
    )


@router.get("/decisions")
def decisions(
    request: Request,
    scope: ReadScope,
    limit: Limit = 50,
    offset: Offset = 0,
) -> dict[str, Any]:
    return _list(
        request,
        scope,
        OperationalEntityKind.DECISION,
        limit=limit,
        offset=offset,
        metric="decisions",
    )


@router.get("/action-requests")
def action_requests(
    request: Request,
    scope: ReadScope,
    limit: Limit = 50,
    offset: Offset = 0,
) -> dict[str, Any]:
    return _list(
        request,
        scope,
        OperationalEntityKind.ACTION_REQUEST,
        limit=limit,
        offset=offset,
        metric="action_requests",
    )


@router.get("/playback")
def playback(
    request: Request,
    scope: ReadScope,
    limit: Limit = 50,
    offset: Offset = 0,
) -> dict[str, Any]:
    result = _list(
        request,
        scope,
        OperationalEntityKind.REPLAY_SNAPSHOT,
        limit=limit,
        offset=offset,
        metric="playback",
    )
    result["causality_claimed"] = False
    result["production_action_claimed"] = False
    return result


@router.get("/evidence")
def evidence(
    request: Request,
    scope: ReadScope,
    limit: Limit = 50,
    offset: Offset = 0,
) -> dict[str, Any]:
    return _list(
        request,
        scope,
        OperationalEntityKind.EVIDENCE_REFERENCE,
        limit=limit,
        offset=offset,
        metric="evidence",
    )


@router.get("/receipts")
def receipts(
    request: Request,
    scope: ReadScope,
    limit: Limit = 50,
    offset: Offset = 0,
) -> dict[str, Any]:
    runtime = get_runtime(request)
    with runtime.metrics.track_query("receipts"):
        rows = runtime.store.list_receipts(scope, limit=limit, offset=offset)
    items = [receipt_record(row) for row in rows]
    return {
        "schema": "szl.lyte.receipt-page/v1",
        "items": items,
        "count": len(items),
        "limit": limit,
        "offset": offset,
        "next_offset": offset + len(items) if len(items) == limit else None,
        "data_mode": _data_mode(runtime, scope),
        "append_only": True,
        "truth_label": "MEASURED",
    }


@router.get("/receipts/{record_hash}")
def receipt(
    record_hash: str,
    request: Request,
    scope: ReadScope,
) -> dict[str, Any]:
    if len(record_hash) != 64 or any(
        character not in "0123456789abcdef" for character in record_hash
    ):
        raise HTTPException(status_code=400, detail="record_hash must be a lowercase SHA-256")
    runtime = get_runtime(request)
    row = runtime.store.get_receipt(scope, record_hash)
    if row is None:
        raise HTTPException(status_code=404, detail="receipt was not found in this workspace")
    return receipt_record(row)


__all__ = ["router"]
