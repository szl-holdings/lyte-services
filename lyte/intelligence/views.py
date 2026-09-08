"""Typed read models for Lyte's service-to-outcome operating views."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from lyte.domain import (
    TruthLabel,
    TruthValue,
    assert_no_sensitive_keys,
    canonical_json,
    isoformat_z,
)

_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._:-]{1,127}$")


def _id(value: str, *, name: str) -> str:
    normalized = str(value).strip()
    if not _IDENTIFIER.fullmatch(normalized):
        raise ValueError(f"{name} must be a stable lowercase identifier")
    return normalized


def _text(value: str, *, name: str, maximum: int = 512) -> str:
    normalized = " ".join(str(value).split())
    if not normalized or len(normalized) > maximum:
        raise ValueError(f"{name} must contain 1-{maximum} characters")
    return normalized


def _refs(values: tuple[str, ...]) -> tuple[str, ...]:
    result = tuple(dict.fromkeys(_text(value, name="evidence ref") for value in values))
    if len(result) > 64:
        raise ValueError("at most 64 evidence references are permitted")
    return result


def _indicators(values: Mapping[str, TruthValue[Any]]) -> dict[str, TruthValue[Any]]:
    result: dict[str, TruthValue[Any]] = {}
    for raw_name, value in values.items():
        name = _id(raw_name, name="indicator name")
        if not isinstance(value, TruthValue):
            raise TypeError(f"indicator {name} must be a TruthValue")
        serialized = value.to_dict()
        assert_no_sensitive_keys(serialized, path=f"indicators.{name}")
        canonical_json(serialized)
        result[name] = value
    return result


def _indicator_dict(values: Mapping[str, TruthValue[Any]]) -> dict[str, dict[str, Any]]:
    return {name: value.to_dict() for name, value in values.items()}


@dataclass(frozen=True, slots=True)
class EvidenceCitation:
    citation_id: str
    title: str
    source_type: str
    source_ref: str
    observed_at: datetime
    truth_label: TruthLabel

    def __post_init__(self) -> None:
        object.__setattr__(self, "citation_id", _id(self.citation_id, name="citation_id"))
        object.__setattr__(self, "title", _text(self.title, name="citation title"))
        object.__setattr__(self, "source_type", _id(self.source_type, name="source_type"))
        object.__setattr__(self, "source_ref", _text(self.source_ref, name="source_ref"))
        isoformat_z(self.observed_at)
        object.__setattr__(self, "truth_label", TruthLabel(self.truth_label))

    def to_dict(self) -> dict[str, Any]:
        return {
            "citation_id": self.citation_id,
            "title": self.title,
            "source_type": self.source_type,
            "source_ref": self.source_ref,
            "observed_at": isoformat_z(self.observed_at),
            "truth_label": self.truth_label.value,
        }


@dataclass(frozen=True, slots=True)
class ServiceView:
    service_id: str
    name: str
    state: str
    indicators: Mapping[str, TruthValue[Any]]
    dependencies: tuple[str, ...] = ()
    deployment_refs: tuple[str, ...] = ()
    incident_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    truth_label: TruthLabel = TruthLabel.UNAVAILABLE

    def __post_init__(self) -> None:
        object.__setattr__(self, "service_id", _id(self.service_id, name="service_id"))
        object.__setattr__(self, "name", _text(self.name, name="service name"))
        object.__setattr__(self, "state", _text(self.state, name="service state", maximum=64))
        object.__setattr__(self, "indicators", _indicators(self.indicators))
        object.__setattr__(
            self,
            "dependencies",
            tuple(_id(value, name="dependency") for value in self.dependencies),
        )
        object.__setattr__(self, "deployment_refs", _refs(self.deployment_refs))
        object.__setattr__(self, "incident_refs", _refs(self.incident_refs))
        object.__setattr__(self, "evidence_refs", _refs(self.evidence_refs))
        object.__setattr__(self, "truth_label", TruthLabel(self.truth_label))

    def to_dict(self) -> dict[str, Any]:
        return {
            "service_id": self.service_id,
            "name": self.name,
            "state": self.state,
            "truth_label": self.truth_label.value,
            "indicators": _indicator_dict(self.indicators),
            "dependencies": list(self.dependencies),
            "deployment_refs": list(self.deployment_refs),
            "incident_refs": list(self.incident_refs),
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True, slots=True)
class JourneyStepView:
    step_id: str
    name: str
    state: str
    service_ids: tuple[str, ...]
    indicators: Mapping[str, TruthValue[Any]]
    evidence_refs: tuple[str, ...]
    truth_label: TruthLabel

    def __post_init__(self) -> None:
        object.__setattr__(self, "step_id", _id(self.step_id, name="step_id"))
        object.__setattr__(self, "name", _text(self.name, name="step name"))
        object.__setattr__(self, "state", _text(self.state, name="step state", maximum=64))
        object.__setattr__(
            self, "service_ids", tuple(_id(value, name="service_id") for value in self.service_ids)
        )
        object.__setattr__(self, "indicators", _indicators(self.indicators))
        object.__setattr__(self, "evidence_refs", _refs(self.evidence_refs))
        object.__setattr__(self, "truth_label", TruthLabel(self.truth_label))

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "name": self.name,
            "state": self.state,
            "truth_label": self.truth_label.value,
            "service_ids": list(self.service_ids),
            "indicators": _indicator_dict(self.indicators),
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True, slots=True)
class JourneyView:
    journey_id: str
    name: str
    state: str
    steps: tuple[JourneyStepView, ...]
    indicators: Mapping[str, TruthValue[Any]]
    service_ids: tuple[str, ...]
    outcome_ids: tuple[str, ...]
    impacted_segments: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    truth_label: TruthLabel

    def __post_init__(self) -> None:
        object.__setattr__(self, "journey_id", _id(self.journey_id, name="journey_id"))
        object.__setattr__(self, "name", _text(self.name, name="journey name"))
        object.__setattr__(self, "state", _text(self.state, name="journey state", maximum=64))
        if not self.steps:
            raise ValueError("journey must contain at least one step")
        object.__setattr__(self, "steps", tuple(self.steps))
        object.__setattr__(self, "indicators", _indicators(self.indicators))
        object.__setattr__(
            self, "service_ids", tuple(_id(value, name="service_id") for value in self.service_ids)
        )
        object.__setattr__(
            self, "outcome_ids", tuple(_id(value, name="outcome_id") for value in self.outcome_ids)
        )
        object.__setattr__(
            self,
            "impacted_segments",
            tuple(
                _text(value, name="impacted segment", maximum=128)
                for value in self.impacted_segments
            ),
        )
        object.__setattr__(self, "evidence_refs", _refs(self.evidence_refs))
        object.__setattr__(self, "truth_label", TruthLabel(self.truth_label))

    def to_dict(self) -> dict[str, Any]:
        return {
            "journey_id": self.journey_id,
            "name": self.name,
            "state": self.state,
            "truth_label": self.truth_label.value,
            "steps": [step.to_dict() for step in self.steps],
            "indicators": _indicator_dict(self.indicators),
            "service_ids": list(self.service_ids),
            "outcome_ids": list(self.outcome_ids),
            "impacted_segments": list(self.impacted_segments),
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True, slots=True)
class OutcomeView:
    outcome_id: str
    name: str
    state: str
    indicators: Mapping[str, TruthValue[Any]]
    service_ids: tuple[str, ...]
    journey_ids: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    truth_label: TruthLabel

    def __post_init__(self) -> None:
        object.__setattr__(self, "outcome_id", _id(self.outcome_id, name="outcome_id"))
        object.__setattr__(self, "name", _text(self.name, name="outcome name"))
        object.__setattr__(self, "state", _text(self.state, name="outcome state", maximum=64))
        object.__setattr__(self, "indicators", _indicators(self.indicators))
        object.__setattr__(
            self, "service_ids", tuple(_id(value, name="service_id") for value in self.service_ids)
        )
        object.__setattr__(
            self, "journey_ids", tuple(_id(value, name="journey_id") for value in self.journey_ids)
        )
        object.__setattr__(self, "evidence_refs", _refs(self.evidence_refs))
        object.__setattr__(self, "truth_label", TruthLabel(self.truth_label))

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome_id": self.outcome_id,
            "name": self.name,
            "state": self.state,
            "truth_label": self.truth_label.value,
            "indicators": _indicator_dict(self.indicators),
            "service_ids": list(self.service_ids),
            "journey_ids": list(self.journey_ids),
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True, slots=True)
class AgentView:
    agent_id: str
    name: str
    state: str
    indicators: Mapping[str, TruthValue[Any]]
    flags: tuple[str, ...]
    service_ids: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    truth_label: TruthLabel

    def __post_init__(self) -> None:
        object.__setattr__(self, "agent_id", _id(self.agent_id, name="agent_id"))
        object.__setattr__(self, "name", _text(self.name, name="agent name"))
        object.__setattr__(self, "state", _text(self.state, name="agent state", maximum=64))
        object.__setattr__(self, "indicators", _indicators(self.indicators))
        object.__setattr__(
            self, "flags", tuple(_id(value, name="agent flag") for value in self.flags)
        )
        object.__setattr__(
            self, "service_ids", tuple(_id(value, name="service_id") for value in self.service_ids)
        )
        object.__setattr__(self, "evidence_refs", _refs(self.evidence_refs))
        object.__setattr__(self, "truth_label", TruthLabel(self.truth_label))

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "state": self.state,
            "truth_label": self.truth_label.value,
            "indicators": _indicator_dict(self.indicators),
            "flags": list(self.flags),
            "service_ids": list(self.service_ids),
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True, slots=True)
class IncidentFactor:
    factor_id: str
    description: str
    relation: str
    evidence_refs: tuple[str, ...]
    causality_claimed: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "factor_id", _id(self.factor_id, name="factor_id"))
        object.__setattr__(self, "description", _text(self.description, name="factor description"))
        relation = str(self.relation).strip().upper()
        if relation not in {"PRECEDED", "CORRELATED", "COINCIDENT", "UNAVAILABLE"}:
            raise ValueError("incident factor relation is unsupported")
        object.__setattr__(self, "relation", relation)
        object.__setattr__(self, "evidence_refs", _refs(self.evidence_refs))
        if self.causality_claimed:
            raise ValueError("candidate incident factors cannot claim causality")

    def to_dict(self) -> dict[str, Any]:
        return {
            "factor_id": self.factor_id,
            "description": self.description,
            "relation": self.relation,
            "causality_claimed": False,
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True, slots=True)
class IncidentView:
    incident_id: str
    title: str
    state: str
    started_at: datetime
    resolved_at: datetime | None
    severity: str
    service_ids: tuple[str, ...]
    journey_ids: tuple[str, ...]
    candidate_factors: tuple[IncidentFactor, ...]
    evidence_refs: tuple[str, ...]
    truth_label: TruthLabel

    def __post_init__(self) -> None:
        object.__setattr__(self, "incident_id", _id(self.incident_id, name="incident_id"))
        object.__setattr__(self, "title", _text(self.title, name="incident title"))
        object.__setattr__(self, "state", _text(self.state, name="incident state", maximum=64))
        isoformat_z(self.started_at)
        if self.resolved_at is not None:
            isoformat_z(self.resolved_at)
            if self.resolved_at < self.started_at:
                raise ValueError("incident resolved_at cannot precede started_at")
        object.__setattr__(self, "severity", _text(self.severity, name="severity", maximum=32))
        object.__setattr__(
            self, "service_ids", tuple(_id(value, name="service_id") for value in self.service_ids)
        )
        object.__setattr__(
            self, "journey_ids", tuple(_id(value, name="journey_id") for value in self.journey_ids)
        )
        object.__setattr__(self, "candidate_factors", tuple(self.candidate_factors))
        object.__setattr__(self, "evidence_refs", _refs(self.evidence_refs))
        object.__setattr__(self, "truth_label", TruthLabel(self.truth_label))

    def to_dict(self) -> dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "title": self.title,
            "state": self.state,
            "severity": self.severity,
            "truth_label": self.truth_label.value,
            "started_at": isoformat_z(self.started_at),
            "resolved_at": isoformat_z(self.resolved_at) if self.resolved_at else None,
            "service_ids": list(self.service_ids),
            "journey_ids": list(self.journey_ids),
            "candidate_factors": [factor.to_dict() for factor in self.candidate_factors],
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True, slots=True)
class PlaybackFrame:
    offset_minutes: int
    observed_at: datetime
    state: str
    changes: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    truth_label: TruthLabel

    def __post_init__(self) -> None:
        if isinstance(self.offset_minutes, bool) or not isinstance(self.offset_minutes, int):
            raise ValueError("offset_minutes must be an integer")
        isoformat_z(self.observed_at)
        object.__setattr__(self, "state", _text(self.state, name="playback state", maximum=64))
        object.__setattr__(
            self,
            "changes",
            tuple(_text(value, name="playback change") for value in self.changes),
        )
        object.__setattr__(self, "evidence_refs", _refs(self.evidence_refs))
        object.__setattr__(self, "truth_label", TruthLabel(self.truth_label))

    def to_dict(self) -> dict[str, Any]:
        return {
            "offset_minutes": self.offset_minutes,
            "observed_at": isoformat_z(self.observed_at),
            "state": self.state,
            "truth_label": self.truth_label.value,
            "changes": list(self.changes),
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True, slots=True)
class ActionRequestView:
    request_id: str
    action_type: str
    state: str
    simulation: bool
    can_execute: bool
    evidence_refs: tuple[str, ...]
    truth_label: TruthLabel

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_id", _id(self.request_id, name="request_id"))
        object.__setattr__(self, "action_type", _id(self.action_type, name="action_type"))
        object.__setattr__(self, "state", _text(self.state, name="action state", maximum=64))
        if not self.simulation or self.can_execute:
            raise ValueError("this release only permits non-executable simulated action requests")
        object.__setattr__(self, "evidence_refs", _refs(self.evidence_refs))
        object.__setattr__(self, "truth_label", TruthLabel(self.truth_label))

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "action_type": self.action_type,
            "state": self.state,
            "simulation": True,
            "can_execute": False,
            "effectors_enabled": False,
            "evidence_refs": list(self.evidence_refs),
            "truth_label": self.truth_label.value,
        }


__all__ = [
    "ActionRequestView",
    "AgentView",
    "EvidenceCitation",
    "IncidentFactor",
    "IncidentView",
    "JourneyStepView",
    "JourneyView",
    "OutcomeView",
    "PlaybackFrame",
    "ServiceView",
]
