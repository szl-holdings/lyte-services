"""Living Anatomy traces expose how evidence moves without storing raw content."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from .canonical import isoformat_z, utc_now
from .receipts import assert_no_sensitive_keys
from .scope import Scope
from .truth import TruthLabel

_OPERATION = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class AnatomyOrgan(StrEnum):
    SOURCE = "source"
    SIGNAL = "signal"
    CORRELATION = "correlation"
    REASONING = "reasoning"
    TRUST_GATE = "trust_gate"
    RECEIPT_BUS = "receipt_bus"
    SECOND_BRAIN = "second_brain"
    EGRESS = "egress"


class TraceState(StrEnum):
    STARTED = "STARTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class AnatomyTrace:
    scope: Scope
    trace_id: UUID
    span_id: UUID
    organ: AnatomyOrgan
    operation: str
    state: TraceState
    truth_label: TruthLabel
    started_at: datetime
    parent_span_id: UUID | None = None
    completed_at: datetime | None = None
    input_sha256: str | None = None
    output_sha256: str | None = None
    attributes: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "organ", AnatomyOrgan(self.organ))
        object.__setattr__(self, "state", TraceState(self.state))
        object.__setattr__(self, "truth_label", TruthLabel(self.truth_label))
        if not _OPERATION.fullmatch(self.operation):
            raise ValueError(f"operation must match {_OPERATION.pattern}")
        isoformat_z(self.started_at)
        if self.completed_at is not None:
            isoformat_z(self.completed_at)
            if self.completed_at < self.started_at:
                raise ValueError("completed_at cannot precede started_at")
        if self.state is TraceState.STARTED and self.completed_at is not None:
            raise ValueError("STARTED traces cannot have completed_at")
        if self.state is not TraceState.STARTED and self.completed_at is None:
            raise ValueError("terminal traces require completed_at")
        for name, digest in (
            ("input_sha256", self.input_sha256),
            ("output_sha256", self.output_sha256),
        ):
            if digest is not None and not _SHA256.fullmatch(digest):
                raise ValueError(f"{name} must be a lowercase SHA-256 digest")
        attributes = dict(self.attributes)
        assert_no_sensitive_keys(attributes, path="attributes")
        object.__setattr__(self, "attributes", attributes)

    @classmethod
    def start(
        cls,
        scope: Scope,
        *,
        organ: AnatomyOrgan,
        operation: str,
        trace_id: UUID | None = None,
        parent_span_id: UUID | None = None,
        truth_label: TruthLabel = TruthLabel.MEASURED,
        input_sha256: str | None = None,
        attributes: Mapping[str, Any] | None = None,
        now: datetime | None = None,
    ) -> AnatomyTrace:
        return cls(
            scope=scope,
            trace_id=trace_id or uuid4(),
            span_id=uuid4(),
            parent_span_id=parent_span_id,
            organ=organ,
            operation=operation,
            state=TraceState.STARTED,
            truth_label=truth_label,
            started_at=now or utc_now(),
            input_sha256=input_sha256,
            attributes=attributes or {},
        )

    def finish(
        self,
        *,
        state: TraceState = TraceState.COMPLETED,
        truth_label: TruthLabel | None = None,
        output_sha256: str | None = None,
        attributes: Mapping[str, Any] | None = None,
        now: datetime | None = None,
    ) -> AnatomyTrace:
        if self.state is not TraceState.STARTED:
            raise ValueError("only STARTED traces can be finished")
        if state is TraceState.STARTED:
            raise ValueError("finish requires a terminal state")
        merged = {**self.attributes, **(attributes or {})}
        return AnatomyTrace(
            scope=self.scope,
            trace_id=self.trace_id,
            span_id=self.span_id,
            parent_span_id=self.parent_span_id,
            organ=self.organ,
            operation=self.operation,
            state=state,
            truth_label=truth_label or self.truth_label,
            started_at=self.started_at,
            completed_at=now or utc_now(),
            input_sha256=self.input_sha256,
            output_sha256=output_sha256,
            attributes=merged,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.scope.to_dict(),
            "trace_id": str(self.trace_id),
            "span_id": str(self.span_id),
            "parent_span_id": str(self.parent_span_id) if self.parent_span_id else None,
            "organ": self.organ.value,
            "operation": self.operation,
            "state": self.state.value,
            "truth_label": self.truth_label.value,
            "started_at": isoformat_z(self.started_at),
            "completed_at": isoformat_z(self.completed_at) if self.completed_at else None,
            "input_sha256": self.input_sha256,
            "output_sha256": self.output_sha256,
            "attributes": dict(self.attributes),
        }
