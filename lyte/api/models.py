"""Strict request contracts for Lyte's bounded API surface."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ShortText = Annotated[str, Field(min_length=1, max_length=256)]
EvidenceRef = Annotated[str, Field(min_length=1, max_length=512)]
EvidenceTruthLabel = Literal[
    "MEASURED",
    "REPORTED",
    "MODELED",
    "SAMPLE",
    "ROADMAP",
    "UNAVAILABLE",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class AskRequest(StrictModel):
    question: Annotated[str, Field(min_length=1, max_length=500)]

    @field_validator("question")
    @classmethod
    def normalize_question(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("question cannot be blank")
        return normalized


class HatunEvaluationRequest(StrictModel):
    action_type: Annotated[str, Field(min_length=1, max_length=128)]
    evidence_labels: list[EvidenceTruthLabel] = Field(default_factory=list, max_length=100)
    missing_evidence: list[ShortText] = Field(default_factory=list, max_length=100)
    policy_violations: list[ShortText] = Field(default_factory=list, max_length=100)
    requests_execution: bool = False
    reversible: bool = True
    risk: Literal["low", "medium", "high", "critical"] = "medium"
    evidence_receipt_ids: list[EvidenceRef] = Field(default_factory=list, max_length=100)
    formula_ids: list[ShortText] = Field(default_factory=list, max_length=100)


class AnalysisRequest(StrictModel):
    service_id: ShortText
    good_events: Annotated[int, Field(ge=0, le=10_000_000_000)]
    total_events: Annotated[int, Field(ge=0, le=10_000_000_000)]
    slo_target: Annotated[float, Field(gt=0.0, lt=1.0)]
    requests: Annotated[int, Field(ge=0, le=10_000_000_000)] = 0
    window_seconds: Annotated[float, Field(gt=0.0, le=2_592_000.0)] = 300.0
    failed_changes: Annotated[int, Field(ge=0, le=10_000_000)] = 0
    total_changes: Annotated[int, Field(ge=0, le=10_000_000)] = 0
    cost_usd: Annotated[float, Field(ge=0.0, le=1_000_000_000.0)] = 0.0
    successful_outcomes: Annotated[int, Field(ge=0, le=10_000_000_000)] = 0
    revenue_volume: Annotated[int, Field(ge=0, le=10_000_000_000)] | None = None
    baseline_conversion_rate: Annotated[float, Field(ge=0.0, le=1.0)] | None = None
    observed_conversion_rate: Annotated[float, Field(ge=0.0, le=1.0)] | None = None
    average_order_value: Annotated[float, Field(ge=0.0, le=100_000_000.0)] | None = None
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")] = "USD"
    evidence_refs: list[EvidenceRef] = Field(default_factory=list, max_length=100)
    input_truth_label: Literal["MEASURED", "REPORTED", "SAMPLE"] = "REPORTED"


__all__ = ["AnalysisRequest", "AskRequest", "HatunEvaluationRequest"]
