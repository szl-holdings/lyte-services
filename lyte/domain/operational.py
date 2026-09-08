"""Allowlisted operational entity contracts for persisted product state."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from .canonical import canonical_json, isoformat_z
from .receipts import assert_no_sensitive_keys
from .truth import TruthLabel

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/~-]{0,255}$")


class OperationalEntityKind(StrEnum):
    SOURCE = "Source"
    SOURCE_CURSOR = "SourceCursor"
    ENTITY = "Entity"
    USER_SUBJECT = "UserSubject"
    ROLE_BINDING = "RoleBinding"
    SERVICE = "Service"
    DEPENDENCY_EDGE = "DependencyEdge"
    CUSTOMER_JOURNEY = "CustomerJourney"
    JOURNEY_STEP = "JourneyStep"
    BUSINESS_OUTCOME = "BusinessOutcome"
    OUTCOME_MEASUREMENT = "OutcomeMeasurement"
    TELEMETRY_WINDOW = "TelemetryWindow"
    AGENT_TRACE_SUMMARY = "AgentTraceSummary"
    DEPLOYMENT_EVENT = "DeploymentEvent"
    INCIDENT = "Incident"
    INCIDENT_EVENT = "IncidentEvent"
    SIGNAL = "Signal"
    RECOMMENDATION = "Recommendation"
    DECISION = "Decision"
    ACTION_REQUEST = "ActionRequest"
    OUTCOME_VERIFICATION = "OutcomeVerification"
    EVIDENCE_REFERENCE = "EvidenceReference"
    REPLAY_SNAPSHOT = "ReplaySnapshot"


@dataclass(frozen=True, slots=True)
class OperationalRecordDraft:
    entity_kind: OperationalEntityKind
    entity_id: str
    name: str
    body: Mapping[str, Any]
    truth_label: TruthLabel
    evidence_refs: tuple[str, ...] = ()
    source_revision: str | None = None
    observed_at: datetime | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "entity_kind", OperationalEntityKind(self.entity_kind))
        object.__setattr__(self, "truth_label", TruthLabel(self.truth_label))
        entity_id = self.entity_id.strip()
        if not _IDENTIFIER.fullmatch(entity_id):
            raise ValueError(f"entity_id must match {_IDENTIFIER.pattern}")
        name = " ".join(self.name.split())
        if not name or len(name) > 256:
            raise ValueError("name must contain 1-256 characters")
        body = dict(self.body)
        metadata = dict(self.metadata)
        assert_no_sensitive_keys(body, path="operational.body")
        assert_no_sensitive_keys(metadata, path="operational.metadata")
        canonical_json(body)
        canonical_json(metadata)
        refs = tuple(dict.fromkeys(item.strip() for item in self.evidence_refs if item.strip()))
        if self.truth_label in {TruthLabel.MEASURED, TruthLabel.REPORTED} and not refs:
            raise ValueError("MEASURED and REPORTED operational records require evidence_refs")
        source_revision = (self.source_revision or "").strip() or None
        if source_revision is not None and not re.fullmatch(
            r"[0-9a-f]{40}|[0-9a-f]{64}", source_revision
        ):
            raise ValueError("source_revision must be a lowercase Git SHA or SHA-256")
        if self.observed_at is not None:
            isoformat_z(self.observed_at)
        object.__setattr__(self, "entity_id", entity_id)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "body", body)
        object.__setattr__(self, "metadata", metadata)
        object.__setattr__(self, "evidence_refs", refs)
        object.__setattr__(self, "source_revision", source_revision)
