"""Bounded, receipt-backed service and economic analysis."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from lyte.api.dependencies import get_mutation_scope, get_runtime
from lyte.api.models import AnalysisRequest
from lyte.api.routes_catalog import ANATOMY
from lyte.api.routes_health import source_revision
from lyte.domain import (
    ReceiptDraft,
    Scope,
    TruthLabel,
    allowed_bad_rate,
    availability_sli,
    change_failure_rate,
    cost_per_success,
    error_budget_burn_rate,
    error_budget_remaining,
    error_rate,
    requests_per_second,
    revenue_at_risk,
    sha256_json,
)

router = APIRouter(prefix="/api/lyte/v2", tags=["analysis"])
MutationScope = Annotated[Scope, Depends(get_mutation_scope)]


def _serialize_formula(result: Any, input_label: TruthLabel) -> dict[str, Any]:
    row = result.to_dict()
    if row["truth_label"] == TruthLabel.MEASURED.value and input_label is not TruthLabel.MEASURED:
        row["truth_label"] = input_label.value
        row["proof_status"] = f"DETERMINISTIC_FROM_{input_label.value}_INPUT"
    return row


@router.post("/analyze")
def analyze(
    payload: AnalysisRequest,
    request: Request,
    scope: MutationScope,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    if idempotency_key is None or not 16 <= len(idempotency_key) <= 256:
        raise HTTPException(
            status_code=400,
            detail="Idempotency-Key must contain 16-256 characters",
        )
    if payload.good_events > payload.total_events:
        raise HTTPException(status_code=422, detail="good_events cannot exceed total_events")
    if payload.failed_changes > payload.total_changes:
        raise HTTPException(status_code=422, detail="failed_changes cannot exceed total_changes")
    if not payload.evidence_refs:
        raise HTTPException(
            status_code=422,
            detail="analysis requires at least one evidence reference",
        )

    input_label = TruthLabel(payload.input_truth_label)
    bad_events = payload.total_events - payload.good_events
    try:
        results = [
            availability_sli(payload.good_events, payload.total_events),
            error_rate(bad_events, payload.total_events),
            allowed_bad_rate(payload.slo_target),
            error_budget_burn_rate(
                payload.good_events,
                payload.total_events,
                payload.slo_target,
            ),
            error_budget_remaining(
                payload.good_events,
                payload.total_events,
                payload.slo_target,
            ),
            requests_per_second(payload.requests, payload.window_seconds),
            change_failure_rate(payload.failed_changes, payload.total_changes),
            cost_per_success(payload.cost_usd, payload.successful_outcomes),
            revenue_at_risk(
                payload.revenue_volume,
                payload.observed_conversion_rate,
                payload.baseline_conversion_rate,
                payload.average_order_value,
            ),
        ]
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    formulas = [_serialize_formula(result, input_label) for result in results]
    by_name = {result["name"]: result for result in formulas}
    input_basis = payload.model_dump(mode="json")
    input_sha = sha256_json(input_basis)
    output_basis = {
        "service_id": payload.service_id,
        "formulas": formulas,
        "causality_claimed": False,
        "effectors_enabled": False,
    }
    output_sha = sha256_json(output_basis)
    revision = source_revision()
    anatomy_trace = [
        {
            "stage": stage,
            "state": "COMPLETE",
            "input_sha256": input_sha,
            "output_sha256": output_sha,
            "truth_label": input_label.value,
            "source_revision": revision,
            "notes": [description],
        }
        for stage, description in ANATOMY
    ]
    runtime = get_runtime(request)
    receipt = runtime.store.append_receipt(
        scope,
        ReceiptDraft(
            kind="analysis.completed",
            subject_type="service",
            subject_id=payload.service_id,
            payload={
                "input_sha256": input_sha,
                "output_sha256": output_sha,
                "formula_ids": [result["name"] for result in formulas],
                "input_truth_label": input_label.value,
                "currency": payload.currency,
                "causality_claimed": False,
                "effectors_enabled": False,
            },
            truth_label=input_label,
            evidence_refs=tuple(payload.evidence_refs),
        ),
        idempotency_key=idempotency_key,
    )
    runtime.metrics.record_receipt("analysis.completed")
    burn = by_name["error_budget_burn_rate"]["value"]
    next_review = (
        "Review the service evidence, dependencies, and most recent deployment."
        if isinstance(burn, (int, float)) and burn > 1
        else None
    )
    return {
        "schema": "szl.lyte.analysis/v2",
        "service_id": payload.service_id,
        "formulas": formulas,
        "anatomy_trace": anatomy_trace,
        "receipt_id": receipt.record_hash,
        "receipt_sequence": receipt.sequence,
        "truth_label": input_label.value,
        "currency": payload.currency,
        "causality_claimed": False,
        "recommended_next_review": next_review,
        "can_authorize": False,
        "can_execute": False,
        "effectors_enabled": False,
    }


__all__ = ["router"]
