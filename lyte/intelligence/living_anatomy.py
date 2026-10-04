"""Machine-enforced Living Anatomy for one bounded analysis execution.

The public stage state describes the result of that stage.  The persisted
``TraceState`` separately records whether the stage implementation itself ran
to a terminal state.  This distinction prevents a partial evidence check or a
human-review decision from being presented as generically "complete".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from lyte.domain import (
    AnatomyOrgan,
    AnatomyTrace,
    Scope,
    TraceState,
    TruthLabel,
    sha256_json,
)

ANALYSIS_ANATOMY_SCHEMA = "szl.lyte.analysis-anatomy/v1"
STAGE_BASIS_SCHEMA = "szl.lyte.analysis-stage-basis/v1"
STAGE_OUTPUT_SCHEMA = "szl.lyte.analysis-stage-output/v1"


@dataclass(frozen=True, slots=True)
class AnalysisStageSpec:
    sequence: int
    stage: str
    description: str
    organ: AnatomyOrgan
    operation: str


ANALYSIS_STAGES = (
    AnalysisStageSpec(
        1,
        "sense",
        "Acquire an allowlisted source or explicit caller observation.",
        AnatomyOrgan.SOURCE,
        "analysis.sense",
    ),
    AnalysisStageSpec(
        2,
        "normalize",
        "Validate request schema, aggregate bounds, units, and currency.",
        AnatomyOrgan.SIGNAL,
        "analysis.normalize",
    ),
    AnalysisStageSpec(
        3,
        "context",
        "Bind authenticated tenant/workspace, declared service, and evidence references.",
        AnatomyOrgan.SIGNAL,
        "analysis.context",
    ),
    AnalysisStageSpec(
        4,
        "formula",
        "Calculate reliability and economic results with explicit availability.",
        AnatomyOrgan.REASONING,
        "analysis.formula",
    ),
    AnalysisStageSpec(
        5,
        "policy",
        "Deny effectors, unsupported authority, and unsupported causality.",
        AnatomyOrgan.TRUST_GATE,
        "analysis.policy",
    ),
    AnalysisStageSpec(
        6,
        "decide",
        "Return REVIEW, ABSTAIN, or DENY for a human owner.",
        AnatomyOrgan.TRUST_GATE,
        "analysis.decide",
    ),
    AnalysisStageSpec(
        7,
        "verify",
        "Check scoped evidence, receipt integrity, and runtime source; mark missing freshness.",
        AnatomyOrgan.TRUST_GATE,
        "analysis.verify",
    ),
    AnalysisStageSpec(
        8,
        "remember",
        "Persist scoped summaries and evidence references, never raw tokens.",
        AnatomyOrgan.SECOND_BRAIN,
        "analysis.remember",
    ),
    AnalysisStageSpec(
        9,
        "receipt",
        "Append a finite canonical record to the scoped hash chain.",
        AnatomyOrgan.RECEIPT_BUS,
        "analysis.receipt",
    ),
)

STAGE_STATES = (
    frozenset({"OBSERVED"}),
    frozenset({"VALIDATED"}),
    frozenset({"BOUND"}),
    frozenset({"CALCULATED", "PARTIAL", "UNAVAILABLE"}),
    frozenset({"ENFORCED", "DENIED"}),
    frozenset({"REVIEW", "ABSTAIN", "DENY"}),
    frozenset({"VERIFIED", "PARTIAL", "FAILED", "UNAVAILABLE"}),
    frozenset({"PERSISTED"}),
    frozenset({"APPENDED"}),
)


@dataclass(frozen=True, slots=True)
class AnalysisStage:
    spec: AnalysisStageSpec
    state: str
    input_sha256: str
    basis_sha256: str
    output_sha256: str
    truth_label: TruthLabel
    source_revision: str | None

    def to_api(self, *, trace_id: UUID | str) -> dict[str, Any]:
        return {
            "sequence": self.spec.sequence,
            "stage": self.spec.stage,
            "operation": self.spec.operation,
            "organ": self.spec.organ.value,
            "state": self.state,
            "execution_state": TraceState.COMPLETED.value,
            "input_sha256": self.input_sha256,
            "basis_sha256": self.basis_sha256,
            "output_sha256": self.output_sha256,
            "truth_label": self.truth_label.value,
            "source_revision": self.source_revision,
            "trace_id": str(trace_id),
            "notes": [self.spec.description],
        }


@dataclass(frozen=True, slots=True)
class AnalysisAnatomy:
    trace_id: UUID
    stages: tuple[AnalysisStage, ...]

    def to_traces(self, scope: Scope) -> tuple[AnatomyTrace, ...]:
        traces: list[AnatomyTrace] = []
        parent_span_id: UUID | None = None
        for stage in self.stages:
            started = AnatomyTrace.start(
                scope,
                organ=stage.spec.organ,
                operation=stage.spec.operation,
                trace_id=self.trace_id,
                parent_span_id=parent_span_id,
                truth_label=stage.truth_label,
                input_sha256=stage.input_sha256,
                attributes={
                    "schema": ANALYSIS_ANATOMY_SCHEMA,
                    "sequence": stage.spec.sequence,
                    "stage": stage.spec.stage,
                    "stage_state": stage.state,
                    "basis_sha256": stage.basis_sha256,
                    "description": stage.spec.description,
                    "source_revision": stage.source_revision,
                    "human_approval_required": True,
                    "can_authorize": False,
                    "can_execute": False,
                    "effectors_enabled": False,
                },
            )
            terminal = started.finish(
                state=TraceState.COMPLETED,
                output_sha256=stage.output_sha256,
            )
            traces.append(terminal)
            parent_span_id = terminal.span_id
        return tuple(traces)


def build_analysis_anatomy(
    *,
    input_sha256: str,
    truth_label: TruthLabel,
    source_revision: str | None,
    stage_results: tuple[tuple[str, dict[str, Any]], ...],
    trace_id: UUID | None = None,
) -> AnalysisAnatomy:
    """Bind nine real transitions into one deterministic digest chain.

    ``stage_results`` contains only bounded, canonicalizable products of the
    actual stage implementation.  The result data is hashed and is not copied
    into trace attributes, preventing the trace stream from becoming a shadow
    store for caller observations.
    """

    if len(stage_results) != len(ANALYSIS_STAGES):
        raise ValueError("analysis anatomy requires exactly nine stage results")
    stages: list[AnalysisStage] = []
    stage_input = input_sha256
    for spec, allowed_states, (state, result) in zip(
        ANALYSIS_STAGES, STAGE_STATES, stage_results, strict=True
    ):
        normalized_state = state.strip().upper()
        if normalized_state not in allowed_states:
            raise ValueError(f"invalid state for analysis stage {spec.stage}")
        basis = {
            "schema": STAGE_BASIS_SCHEMA,
            "sequence": spec.sequence,
            "stage": spec.stage,
            "operation": spec.operation,
            "input_sha256": stage_input,
            "truth_label": truth_label.value,
            "source_revision": source_revision,
        }
        basis_sha256 = sha256_json(basis)
        output_sha256 = sha256_json(
            {
                "schema": STAGE_OUTPUT_SCHEMA,
                "stage": spec.stage,
                "state": normalized_state,
                "basis_sha256": basis_sha256,
                "result": result,
            }
        )
        stages.append(
            AnalysisStage(
                spec=spec,
                state=normalized_state,
                input_sha256=stage_input,
                basis_sha256=basis_sha256,
                output_sha256=output_sha256,
                truth_label=truth_label,
                source_revision=source_revision,
            )
        )
        stage_input = output_sha256
    return AnalysisAnatomy(trace_id=trace_id or uuid4(), stages=tuple(stages))


def stored_trace_to_api(row: Any) -> dict[str, Any]:
    """Project one persisted anatomy row back to its public stage contract."""

    attributes = dict(row.attributes_json)
    return {
        "sequence": attributes["sequence"],
        "stage": attributes["stage"],
        "operation": row.operation,
        "organ": row.organ,
        "state": attributes["stage_state"],
        "execution_state": row.state,
        "input_sha256": row.input_sha256,
        "basis_sha256": attributes["basis_sha256"],
        "output_sha256": row.output_sha256,
        "truth_label": row.truth_label,
        "source_revision": attributes.get("source_revision"),
        "trace_id": row.trace_id,
        "span_id": row.span_id,
        "parent_span_id": row.parent_span_id,
        "notes": [attributes["description"]],
    }


__all__ = [
    "ANALYSIS_ANATOMY_SCHEMA",
    "ANALYSIS_STAGES",
    "AnalysisAnatomy",
    "AnalysisStage",
    "AnalysisStageSpec",
    "build_analysis_anatomy",
    "stored_trace_to_api",
]
