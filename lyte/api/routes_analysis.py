"""Bounded, receipt-backed service and economic analysis."""

from __future__ import annotations

import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.exc import SQLAlchemyError

from lyte.api import routes_forecast, routes_forecast_workbench
from lyte.api.dependencies import get_mutation_scope, get_runtime
from lyte.api.models import AnalysisRequest
from lyte.api.routes_health import source_identity
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
from lyte.governance import MemoryDraft, MemoryKind, enterprise_memory_partition
from lyte.intelligence import (
    ANALYSIS_ANATOMY_SCHEMA,
    build_analysis_anatomy,
    stored_trace_to_api,
)
from lyte.persistence import STREAM_RECEIPTS, IdempotencyConflict, PersistenceError

router = APIRouter(prefix="/api/lyte/v2", tags=["analysis"])
MutationScope = Annotated[Scope, Depends(get_mutation_scope)]
_IDEMPOTENCY_CONFLICT_DETAIL = "Idempotency-Key was already used for a different request"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SUBJECT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/~-]{0,255}$")


def _serialize_formula(result: Any, input_label: TruthLabel) -> dict[str, Any]:
    row = result.to_dict()
    if row["truth_label"] == TruthLabel.MEASURED.value and input_label is not TruthLabel.MEASURED:
        row["truth_label"] = input_label.value
        row["proof_status"] = f"DETERMINISTIC_FROM_{input_label.value}_INPUT"
    return row


def _format_observation(value: Any, *, unit: str = "") -> str:
    if not isinstance(value, (int, float)):
        return "UNAVAILABLE"
    suffix = f" {unit}" if unit else ""
    return f"{value:.6g}{suffix}"


def _verify_analysis_boundary(
    runtime: Any,
    scope: Scope,
    evidence_refs: list[str],
    *,
    source: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    verified: list[str] = []
    unresolved: list[str] = []
    for evidence_ref in evidence_refs:
        if _SHA256.fullmatch(evidence_ref) and runtime.store.get_receipt(scope, evidence_ref):
            verified.append(evidence_ref)
        else:
            unresolved.append(evidence_ref)
    chain = runtime.store.verify_chain(scope, STREAM_RECEIPTS)
    source_bound = bool(source["bindings_agree"] and source["revision"])
    # AnalysisRequest has no observed-at/source-version contract. Even when the
    # runtime is exactly source-bound, external source freshness is unavailable.
    source_freshness = "UNAVAILABLE"
    fully_verified = (
        not unresolved
        and len(verified) == len(evidence_refs)
        and chain.valid
        and source_bound
        and source_freshness == "VERIFIED"
    )
    return (
        "VERIFIED" if fully_verified else "PARTIAL",
        {
            "evidence": "VERIFIED_SCOPED" if not unresolved else "PARTIAL",
            "verified_scoped_receipt_count": len(verified),
            "unresolved_evidence_count": len(unresolved),
            "unresolved_evidence_sha256": [sha256_json(item) for item in unresolved],
            "invariants": "VERIFIED",
            "receipt_chain": "VERIFIED" if chain.valid else "FAILED",
            "receipt_chain_record_count": chain.record_count,
            "source_binding": source["state"],
            "source_freshness": source_freshness,
            "causality_verified": False,
            "execution_verified": False,
        },
    )


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

    runtime = get_runtime(request)
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
    burn = by_name["error_budget_burn_rate"]["value"]
    decision = (
        "REVIEW"
        if isinstance(burn, (int, float)) and burn > 1
        else "ABSTAIN"
    )
    next_review = (
        "Review the service evidence, dependencies, and most recent deployment."
        if decision == "REVIEW"
        else None
    )
    source = source_identity()
    try:
        verification_state, verification = _verify_analysis_boundary(
            runtime,
            scope,
            payload.evidence_refs,
            source=source,
        )
    except (PersistenceError, SQLAlchemyError) as exc:
        raise HTTPException(
            status_code=503,
            detail="analysis evidence verification is unavailable",
        ) from exc
    formula_state = (
        "CALCULATED"
        if all(result["truth_label"] != TruthLabel.UNAVAILABLE.value for result in formulas)
        else "PARTIAL"
    )
    formula_output_sha256 = sha256_json(formulas)
    policy_result = {
        "authority_mode": "HUMAN_SOVEREIGN",
        "human_approval_required": True,
        "can_authorize": False,
        "can_execute": False,
        "effectors_enabled": False,
        "causality_claimed": False,
    }
    decision_result = {
        "decision": decision,
        "owner": "HUMAN",
        "recommended_next_review": next_review,
        **policy_result,
    }
    memory_summary = (
        f"Observed analysis for service {payload.service_id}: availability "
        f"{_format_observation(by_name['availability_sli']['value'])}, error-budget burn "
        f"{_format_observation(burn, unit='x')}; Lyte returned {decision} for human "
        "review and did not authorize or execute an action."
    )
    memory_semantics = {
        "kind": MemoryKind.OBSERVATION.value,
        "summary_sha256": sha256_json(memory_summary),
        "subjects": [payload.service_id],
        "approved_knowledge": False,
        "raw_request_persisted": False,
        "raw_session_material_persisted": False,
        "partition_sha256": enterprise_memory_partition(scope),
    }
    receipt_semantics = {
        "kind": "analysis.completed",
        "subject_type": "service",
        "subject_id_sha256": sha256_json(payload.service_id),
        "append_only": True,
        "idempotency_enforced": True,
    }
    anatomy = build_analysis_anatomy(
        input_sha256=input_sha,
        truth_label=input_label,
        source_revision=source["revision"],
        stage_results=(
            (
                "OBSERVED",
                {
                    "observation_type": "EXPLICIT_CALLER_AGGREGATES",
                    "input_sha256": input_sha,
                    "evidence_ref_count": len(payload.evidence_refs),
                    "raw_evidence_persisted": False,
                },
            ),
            (
                "VALIDATED",
                {
                    "normalized_input_sha256": input_sha,
                    "schema_validated": True,
                    "bounds_validated": True,
                    "units": {"cost": payload.currency, "window": "seconds"},
                },
            ),
            (
                "BOUND",
                {
                    "scope_sha256": sha256_json(scope.to_dict()),
                    "service_id_sha256": sha256_json(payload.service_id),
                    "evidence_refs_sha256": sha256_json(payload.evidence_refs),
                },
            ),
            (
                formula_state,
                {
                    "formula_output_sha256": formula_output_sha256,
                    "formula_count": len(formulas),
                    "available_formula_count": sum(
                        result["truth_label"] != TruthLabel.UNAVAILABLE.value
                        for result in formulas
                    ),
                },
            ),
            ("ENFORCED", policy_result),
            (decision, decision_result),
            (verification_state, verification),
            ("PERSISTED", memory_semantics),
            ("APPENDED", receipt_semantics),
        ),
    )
    anatomy_sha256 = sha256_json(
        [
            {
                "sequence": stage.spec.sequence,
                "stage": stage.spec.stage,
                "state": stage.state,
                "input_sha256": stage.input_sha256,
                "basis_sha256": stage.basis_sha256,
                "output_sha256": stage.output_sha256,
            }
            for stage in anatomy.stages
        ]
    )
    receipt_draft = ReceiptDraft(
        kind="analysis.completed",
        subject_type="service",
        subject_id=payload.service_id,
        payload={
            "input_sha256": input_sha,
            "output_sha256": output_sha,
            "formula_output_sha256": formula_output_sha256,
            "formula_ids": [result["name"] for result in formulas],
            "input_truth_label": input_label.value,
            "currency": payload.currency,
            "decision": decision,
            "verification_state": verification_state,
            "trace_id": str(anatomy.trace_id),
            "anatomy_schema": ANALYSIS_ANATOMY_SCHEMA,
            "anatomy_sha256": anatomy_sha256,
            "source_revision": source["revision"],
            "human_approval_required": True,
            "can_authorize": False,
            "can_execute": False,
            "causality_claimed": False,
            "effectors_enabled": False,
        },
        truth_label=input_label,
        evidence_refs=tuple(payload.evidence_refs),
    )
    memory_draft = MemoryDraft(
        kind=MemoryKind.OBSERVATION,
        summary=memory_summary,
        truth_label=input_label,
        evidence_refs=tuple(payload.evidence_refs),
        subjects=(payload.service_id,),
        subject_dimensions=(
            {"service": (payload.service_id,)}
            if _SUBJECT_ID.fullmatch(payload.service_id)
            else {}
        ),
        metadata={
            "trace_id": str(anatomy.trace_id),
            "analysis_input_sha256": input_sha,
            "analysis_output_sha256": output_sha,
            "decision": decision,
            "human_approval_required": True,
            "approved_knowledge": False,
            "can_authorize": False,
            "can_execute": False,
            "causality_claimed": False,
            "effectors_enabled": False,
        },
    )
    request_digest = sha256_json(
        {
            "schema": "szl.lyte.analysis-request-idempotency/v1",
            "payload": input_basis,
        }
    )
    try:
        bundle = runtime.store.append_analysis_bundle(
            scope,
            receipt_draft,
            memory_draft,
            anatomy.to_traces(scope),
            idempotency_key=idempotency_key,
            request_digest=request_digest,
        )
    except IdempotencyConflict as exc:
        raise HTTPException(status_code=409, detail=_IDEMPOTENCY_CONFLICT_DETAIL) from exc
    except PersistenceError as exc:
        raise HTTPException(
            status_code=503,
            detail="analysis durable bundle could not be committed",
        ) from exc
    if (
        bundle.receipt.payload_json.get("formula_output_sha256") != formula_output_sha256
        or bundle.receipt.payload_json.get("output_sha256") != output_sha
    ):
        raise HTTPException(
            status_code=503,
            detail="analysis output differs from its original durable receipt",
        )
    if not bundle.idempotent_replay:
        runtime.metrics.record_receipt("analysis.completed")
    anatomy_trace = [stored_trace_to_api(row) for row in bundle.traces]
    return {
        "schema": "szl.lyte.analysis/v2",
        "service_id": payload.service_id,
        "formulas": formulas,
        "anatomy_trace": anatomy_trace,
        "trace_id": bundle.receipt.payload_json["trace_id"],
        "anatomy_schema": ANALYSIS_ANATOMY_SCHEMA,
        "anatomy_persisted": True,
        "memory_record_hash": bundle.memory.record_hash,
        "memory_persisted": True,
        "receipt_id": bundle.receipt.record_hash,
        "receipt_sequence": bundle.receipt.sequence,
        "idempotent_replay": bundle.idempotent_replay,
        "truth_label": input_label.value,
        "currency": payload.currency,
        "decision": bundle.receipt.payload_json["decision"],
        "verification_state": bundle.receipt.payload_json["verification_state"],
        "causality_claimed": False,
        "recommended_next_review": (
            "Review the service evidence, dependencies, and most recent deployment."
            if bundle.receipt.payload_json["decision"] == "REVIEW"
            else None
        ),
        "human_approval_required": True,
        "can_authorize": False,
        "can_execute": False,
        "effectors_enabled": False,
    }


@router.post("/forecast")
def forecast(payload: routes_forecast.ForecastBody) -> dict[str, object]:
    """Expose Forecast Loom through Lyte's already-mounted analysis router."""
    return routes_forecast.forecast(payload)


router.include_router(routes_forecast_workbench.router)

__all__ = ["router"]
