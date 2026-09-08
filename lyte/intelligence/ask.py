"""Deterministic, citation-first Ask Lyte answer engine."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from lyte.domain import TruthLabel, canonical_json

from .scenario import CheckoutScenario, build_checkout_scenario
from .views import EvidenceCitation


@dataclass(frozen=True, slots=True)
class AskLyteAnswer:
    citations: tuple[EvidenceCitation, ...]
    question: str
    intent: str
    answer: str | None
    truth_label: TruthLabel
    evidence_summary: tuple[str, ...] = ()
    formulas: tuple[Mapping[str, Any], ...] = ()
    recommendation: str | None = None
    missing_evidence: tuple[str, ...] = ()
    reason: str | None = None
    can_execute: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "citations", tuple(self.citations))
        object.__setattr__(self, "truth_label", TruthLabel(self.truth_label))
        object.__setattr__(self, "evidence_summary", tuple(self.evidence_summary))
        object.__setattr__(self, "formulas", tuple(dict(item) for item in self.formulas))
        object.__setattr__(self, "missing_evidence", tuple(self.missing_evidence))
        if self.can_execute:
            raise ValueError("Ask Lyte cannot grant execution authority")
        if self.truth_label is TruthLabel.UNAVAILABLE:
            if self.answer is not None:
                raise ValueError("UNAVAILABLE answers cannot contain a factual answer")
            if not self.reason:
                raise ValueError("UNAVAILABLE answers require a reason")
        elif not self.answer or not self.citations:
            raise ValueError("available answers require text and at least one citation")
        canonical_json(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        """Serialize citations first so clients can render evidence before prose."""

        return {
            "citations": [citation.to_dict() for citation in self.citations],
            "question": self.question,
            "intent": self.intent,
            "answer": self.answer,
            "truth_label": self.truth_label.value,
            "evidence_summary": list(self.evidence_summary),
            "formulas": [dict(item) for item in self.formulas],
            "recommendation": self.recommendation,
            "missing_evidence": list(self.missing_evidence),
            "reason": self.reason,
            "can_execute": False,
        }


class AskLyteEngine:
    """Answer the bounded enterprise-demo question set without an LLM."""

    def __init__(self, scenario: CheckoutScenario | None = None) -> None:
        self.scenario = scenario or build_checkout_scenario()

    def answer(self, question: str) -> AskLyteAnswer:
        clean = " ".join(str(question).split())
        if not clean or len(clean) > 500:
            raise ValueError("question must contain 1-500 characters")
        normalized = re.sub(r"[^a-z0-9]+", " ", clean.lower()).strip()

        if "improved" in normalized and "action" in normalized:
            return self._post_action(clean)
        if "evidence" in normalized and "formula" in normalized:
            return self._formula_proof(clean)
        if "review first" in normalized or "operator review" in normalized:
            return self._review_first(clean)
        if "deployment" in normalized and "incident" in normalized:
            return self._deployment_incident(clean)
        if "agent" in normalized and ("expensive" in normalized or "unreliable" in normalized):
            return self._agent_cost_reliability(clean)
        if "changed" in normalized and "journey" in normalized:
            return self._journey_change(clean)
        if "error budget" in normalized and "service" in normalized:
            return self._error_budget(clean)
        if "checkout" in normalized and ("revenue" in normalized or "risk" in normalized):
            return self._revenue_risk(clean)
        return AskLyteAnswer(
            citations=(),
            question=clean,
            intent="unsupported",
            answer=None,
            truth_label=TruthLabel.UNAVAILABLE,
            missing_evidence=("a deterministic query definition for this question",),
            reason="Ask Lyte supports a bounded, documented question set in this release",
        )

    def _citations(self, *citation_ids: str) -> tuple[EvidenceCitation, ...]:
        by_id = self.scenario.evidence_by_id
        try:
            return tuple(by_id[citation_id] for citation_id in citation_ids)
        except KeyError as exc:
            raise RuntimeError(f"scenario is missing citation {exc.args[0]}") from exc

    def _revenue_risk(self, question: str) -> AskLyteAnswer:
        refs = (
            "sample:journey-window:checkout:20260904t1200z",
            "model:revenue-at-risk:v1",
            "sample:service-window:checkout:20260904t1200z",
        )
        return AskLyteAnswer(
            citations=self._citations(*refs),
            question=question,
            intent="checkout_revenue_risk",
            answer=(
                "The SAMPLE checkout conversion rate is 0.61 versus a 0.72 fixture baseline. "
                "With 100,000 fixture attempts and a SAMPLE $84 average order value, the declared "
                "model estimates $924,000 at risk; this is MODELED, not observed revenue loss "
                "[sample:journey-window:checkout:20260904t1200z] "
                "[model:revenue-at-risk:v1]."
            ),
            truth_label=TruthLabel.MODELED,
            evidence_summary=(
                "Checkout error-budget burn is 14x in the SAMPLE service window.",
                "Payment p95 and checkout abandonment rise in the same SAMPLE window.",
            ),
            formulas=(
                {
                    "name": "revenue_at_risk",
                    "expression": (
                        "volume * max(0, baseline_conversion_rate - "
                        "observed_conversion_rate) * average_order_value_usd"
                    ),
                    "inputs": {
                        "volume": 100_000,
                        "baseline_conversion_rate": 0.72,
                        "observed_conversion_rate": 0.61,
                        "average_order_value_usd": 84.0,
                    },
                    "value": 924_000.0,
                    "truth_label": "MODELED",
                    "can_authorize": False,
                },
            ),
            recommendation=(
                "Review checkout and payment evidence; do not treat correlation as cause."
            ),
        )

    def _error_budget(self, question: str) -> AskLyteAnswer:
        ref = "sample:service-window:checkout:20260904t1200z"
        return AskLyteAnswer(
            citations=self._citations(ref),
            question=question,
            intent="highest_error_budget_burn",
            answer=(
                "Among fixture services with error-budget evidence, checkout-api has the highest "
                "burn at 14x against the SAMPLE 99.9% target. No comparable burn value is present "
                "for payment-api [sample:service-window:checkout:20260904t1200z]."
            ),
            truth_label=TruthLabel.SAMPLE,
            evidence_summary=("98,600 good events out of 100,000 SAMPLE requests.",),
            formulas=(
                {
                    "name": "error_budget_burn_rate",
                    "expression": "(1 - availability_sli) / (1 - target)",
                    "value": 14.0,
                    "truth_label": "SAMPLE",
                },
            ),
        )

    def _journey_change(self, question: str) -> AskLyteAnswer:
        refs = (
            "sample:github:run:1842",
            "sample:service-window:payment:20260904t1200z",
            "sample:journey-window:checkout:20260904t1200z",
        )
        return AskLyteAnswer(
            citations=self._citations(*refs),
            question=question,
            intent="pre_journey_change",
            answer=(
                "Sample deployment run 1842 completed 15 minutes before the degraded fixture "
                "window. Payment p95 then reached 1,250 ms while checkout completion was 0.61. "
                "The sequence is PRECEDED/CORRELATED evidence and does not establish causality "
                "[sample:github:run:1842] "
                "[sample:service-window:payment:20260904t1200z] "
                "[sample:journey-window:checkout:20260904t1200z]."
            ),
            truth_label=TruthLabel.SAMPLE,
            evidence_summary=("No causal experiment or production rollback result is available.",),
        )

    def _agent_cost_reliability(self, question: str) -> AskLyteAnswer:
        ref = "sample:agent-window:support:20260904t1200z"
        return AskLyteAnswer(
            citations=self._citations(ref),
            question=question,
            intent="agent_cost_reliability",
            answer=(
                "Support Copilot is the only AI agent in this bounded fixture. Its SAMPLE window "
                "shows 0.78 success, 4,800 ms p95 latency, $0.42 per trace, and 0.12 tool-failure "
                "rate [sample:agent-window:support:20260904t1200z]."
            ),
            truth_label=TruthLabel.SAMPLE,
            evidence_summary=("No cross-agent ranking is possible with one fixture agent.",),
            recommendation="Review tool failures and cost drivers before changing the agent.",
        )

    def _deployment_incident(self, question: str) -> AskLyteAnswer:
        refs = (
            "sample:github:run:1842",
            "sample:service-window:checkout:20260904t1200z",
        )
        return AskLyteAnswer(
            citations=self._citations(*refs),
            question=question,
            intent="incident_deployment_correlation",
            answer=(
                "Sample run 1842 is the deployment that precedes the incident window by 15 "
                "minutes. Lyte labels it a candidate factor only; the fixture has no evidence "
                "that the deployment caused the degradation [sample:github:run:1842] "
                "[sample:service-window:checkout:20260904t1200z]."
            ),
            truth_label=TruthLabel.SAMPLE,
            evidence_summary=("causality_claimed=false",),
        )

    def _review_first(self, question: str) -> AskLyteAnswer:
        refs = (
            "sample:service-window:checkout:20260904t1200z",
            "sample:service-window:payment:20260904t1200z",
            "sample:github:run:1842",
            "sample:hatun-review:checkout:1",
        )
        return AskLyteAnswer(
            citations=self._citations(*refs),
            question=question,
            intent="operator_review_priority",
            answer=(
                "Review the checkout error-budget window and payment latency alongside run 1842 "
                "first. Hatun's SAMPLE result is REVIEW, and the rollback request remains "
                "NOT_EXECUTED [sample:hatun-review:checkout:1]."
            ),
            truth_label=TruthLabel.SAMPLE,
            evidence_summary=(
                "The service, dependency, deployment, and journey windows align temporally.",
                "Temporal alignment is not causal proof.",
            ),
            recommendation="Assign a human owner to validate the candidate factor.",
        )

    def _formula_proof(self, question: str) -> AskLyteAnswer:
        refs = (
            "model:revenue-at-risk:v1",
            "sample:journey-window:checkout:20260904t1200z",
            "sample:service-window:checkout:20260904t1200z",
        )
        return AskLyteAnswer(
            citations=self._citations(*refs),
            question=question,
            intent="evidence_and_formulas",
            answer=(
                "The recommendation is backed by the SAMPLE checkout service and journey windows, "
                "plus a declared MODELED revenue formula. Neither formula grants action authority "
                "[model:revenue-at-risk:v1]."
            ),
            truth_label=TruthLabel.MODELED,
            evidence_summary=(
                "SAMPLE availability: 0.986 from 98,600/100,000.",
                "SAMPLE conversion: 0.61 versus 0.72 fixture baseline.",
            ),
            formulas=(
                {
                    "name": "error_budget_burn_rate",
                    "expression": "(1 - availability_sli) / (1 - target)",
                    "value": 14.0,
                    "truth_label": "SAMPLE",
                },
                {
                    "name": "revenue_at_risk",
                    "expression": (
                        "volume * max(0, baseline_conversion_rate - "
                        "observed_conversion_rate) * average_order_value_usd"
                    ),
                    "value": 924_000.0,
                    "truth_label": "MODELED",
                },
            ),
        )

    def _post_action(self, question: str) -> AskLyteAnswer:
        refs = ("sample:hatun-review:checkout:1",)
        return AskLyteAnswer(
            citations=self._citations(*refs),
            question=question,
            intent="post_action_outcome",
            answer=None,
            truth_label=TruthLabel.UNAVAILABLE,
            evidence_summary=(
                "The fixture contains a simulated rollback request and an expected-recovery frame.",
            ),
            missing_evidence=(
                "an approved action",
                "a witnessed execution receipt",
                "a production verification window",
            ),
            reason=(
                "no action was approved or executed, so improvement after an approved action "
                "cannot be claimed"
            ),
        )


def ask_lyte(question: str, scenario: CheckoutScenario | None = None) -> AskLyteAnswer:
    return AskLyteEngine(scenario).answer(question)


answer_checkout_question = ask_lyte

__all__ = [
    "AskLyteAnswer",
    "AskLyteEngine",
    "answer_checkout_question",
    "ask_lyte",
]
