"""Hatun is a review gate, never an action executor."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from lyte.domain import TruthLabel


class HatunDecision(StrEnum):
    REVIEW = "REVIEW"
    ABSTAIN = "ABSTAIN"
    DENY = "DENY"


@dataclass(frozen=True, slots=True)
class HatunRequest:
    action_type: str
    evidence_labels: tuple[TruthLabel, ...]
    missing_evidence: tuple[str, ...] = ()
    policy_violations: tuple[str, ...] = ()
    requests_execution: bool = False
    reversible: bool = True
    risk: str = "medium"

    def __post_init__(self) -> None:
        action_type = self.action_type.strip().lower()
        if not action_type or len(action_type) > 128:
            raise ValueError("action_type must contain 1-128 characters")
        risk = self.risk.strip().lower()
        if risk not in {"low", "medium", "high", "critical"}:
            raise ValueError("risk must be low, medium, high, or critical")
        object.__setattr__(self, "action_type", action_type)
        object.__setattr__(self, "risk", risk)
        object.__setattr__(
            self,
            "evidence_labels",
            tuple(TruthLabel(item) for item in self.evidence_labels),
        )
        object.__setattr__(
            self,
            "missing_evidence",
            tuple(dict.fromkeys(item.strip() for item in self.missing_evidence if item.strip())),
        )
        object.__setattr__(
            self,
            "policy_violations",
            tuple(dict.fromkeys(item.strip() for item in self.policy_violations if item.strip())),
        )


@dataclass(frozen=True, slots=True)
class HatunReview:
    decision: HatunDecision
    reasons: tuple[str, ...]
    truth_label: TruthLabel = TruthLabel.MODELED
    can_execute: bool = False
    human_decision_required: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "decision", HatunDecision(self.decision))
        object.__setattr__(self, "truth_label", TruthLabel(self.truth_label))
        if self.can_execute:
            raise ValueError("Hatun cannot execute actions")
        if not self.reasons:
            raise ValueError("Hatun reviews require at least one reason")

    def to_dict(self) -> dict[str, object]:
        return {
            "decision": self.decision.value,
            "reasons": list(self.reasons),
            "truth_label": self.truth_label.value,
            "can_execute": False,
            "human_decision_required": self.human_decision_required,
        }


def evaluate_hatun(request: HatunRequest) -> HatunReview:
    deny_reasons: list[str] = []
    if request.requests_execution:
        deny_reasons.append("production effectors are disabled in this release")
    if not request.reversible:
        deny_reasons.append("irreversible actions are outside the review boundary")
    if request.policy_violations:
        deny_reasons.extend(
            f"policy violation: {violation}" for violation in request.policy_violations
        )
    if request.risk == "critical":
        deny_reasons.append("critical-risk actions require a separate protected release")
    if deny_reasons:
        return HatunReview(HatunDecision.DENY, tuple(deny_reasons))

    unavailable = {
        TruthLabel.UNAVAILABLE,
        TruthLabel.ROADMAP,
    }
    unusable_labels = sorted(
        {label.value for label in request.evidence_labels if label in unavailable}
    )
    abstain_reasons = [f"missing evidence: {name}" for name in request.missing_evidence]
    if not request.evidence_labels:
        abstain_reasons.append("no evidence was supplied")
    if unusable_labels:
        abstain_reasons.append("evidence is not production-observed: " + ", ".join(unusable_labels))
    if abstain_reasons:
        return HatunReview(HatunDecision.ABSTAIN, tuple(abstain_reasons))

    sample_review = TruthLabel.SAMPLE in request.evidence_labels
    reasons = [
        "evidence is sufficient for human review",
        "correlation and modeled scores do not establish causality",
    ]
    if sample_review:
        reasons.append("sample evidence is demonstration-only and cannot support production action")
    return HatunReview(
        HatunDecision.REVIEW,
        tuple(reasons),
        truth_label=TruthLabel.SAMPLE if sample_review else TruthLabel.MODELED,
    )


def labels(values: Iterable[TruthLabel | str]) -> tuple[TruthLabel, ...]:
    return tuple(TruthLabel(value) for value in values)
