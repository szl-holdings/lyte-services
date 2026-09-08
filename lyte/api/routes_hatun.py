"""Hatun human-sovereign review boundary."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from lyte.api.dependencies import get_runtime
from lyte.api.models import HatunEvaluationRequest
from lyte.governance import HatunRequest, evaluate_hatun

router = APIRouter(prefix="/api/lyte/v2", tags=["governance"])


@router.post("/hatun/evaluate")
def evaluate(payload: HatunEvaluationRequest, request: Request) -> dict[str, Any]:
    runtime = get_runtime(request)
    with runtime.metrics.track_query("hatun"):
        review = evaluate_hatun(
            HatunRequest(
                action_type=payload.action_type,
                evidence_labels=tuple(payload.evidence_labels),
                missing_evidence=tuple(payload.missing_evidence),
                policy_violations=tuple(payload.policy_violations),
                requests_execution=payload.requests_execution,
                reversible=payload.reversible,
                risk=payload.risk,
            )
        )
    blockers = list(review.reasons) if review.decision.value != "REVIEW" else []
    return {
        "schema": "szl.hatun-policy/v1",
        "authority_mode": "HUMAN_SOVEREIGN",
        "default_disposition": "DENY_EFFECTOR",
        "decision": review.decision.value,
        "reasons": list(review.reasons),
        "blockers": blockers,
        "evidence_receipt_ids": payload.evidence_receipt_ids,
        "formula_ids": payload.formula_ids,
        "lambda_status": "CONJECTURE_1_ADVISORY",
        "truth_label": review.truth_label.value,
        "can_authorize": False,
        "can_execute": False,
        "effectors_enabled": False,
        "human_approval_required": True,
        "session_token_recorded": False,
        "credential_material_recorded": False,
    }


__all__ = ["router"]
