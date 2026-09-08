"""Deterministic, explicitly SAMPLE/MODELED checkout intelligence fixture."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from lyte.domain import (
    TruthLabel,
    TruthValue,
    error_budget_burn_rate,
    journey_health,
    revenue_at_risk,
    sha256_json,
)

from .views import (
    ActionRequestView,
    AgentView,
    EvidenceCitation,
    IncidentFactor,
    IncidentView,
    JourneyStepView,
    JourneyView,
    OutcomeView,
    PlaybackFrame,
    ServiceView,
)

SAMPLE_SCENARIO_ID = "sample.checkout-degradation.v1"
_ANCHOR = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)


def _sample(
    value: Any,
    *refs: str,
    observed_at: datetime = _ANCHOR,
    metadata: dict[str, Any] | None = None,
) -> TruthValue[Any]:
    return TruthValue(
        value=value,
        label=TruthLabel.SAMPLE,
        evidence_refs=tuple(refs),
        observed_at=observed_at,
        metadata=metadata or {},
    )


def _modeled(
    value: Any,
    *refs: str,
    observed_at: datetime = _ANCHOR,
    metadata: dict[str, Any] | None = None,
) -> TruthValue[Any]:
    return TruthValue(
        value=value,
        label=TruthLabel.MODELED,
        evidence_refs=tuple(refs),
        observed_at=observed_at,
        metadata=metadata or {},
    )


@dataclass(frozen=True, slots=True)
class CheckoutScenario:
    scenario_id: str
    generated_at: datetime
    citations: tuple[EvidenceCitation, ...]
    services: tuple[ServiceView, ...]
    journeys: tuple[JourneyView, ...]
    outcomes: tuple[OutcomeView, ...]
    agents: tuple[AgentView, ...]
    incidents: tuple[IncidentView, ...]
    playback: tuple[PlaybackFrame, ...]
    action_requests: tuple[ActionRequestView, ...]

    @property
    def evidence_by_id(self) -> dict[str, EvidenceCitation]:
        return {citation.citation_id: citation for citation in self.citations}

    @property
    def scenario_sha256(self) -> str:
        return sha256_json(self.to_dict(include_digest=False))

    def to_dict(self, *, include_digest: bool = True) -> dict[str, Any]:
        result = {
            "scenario_id": self.scenario_id,
            "data_mode": "SAMPLE",
            "allowed_truth_labels": ["SAMPLE", "MODELED", "UNAVAILABLE"],
            "generated_at": self.generated_at.isoformat().replace("+00:00", "Z"),
            "effectors_enabled": False,
            "causality_claimed": False,
            "citations": [citation.to_dict() for citation in self.citations],
            "services": [service.to_dict() for service in self.services],
            "journeys": [journey.to_dict() for journey in self.journeys],
            "outcomes": [outcome.to_dict() for outcome in self.outcomes],
            "agents": [agent.to_dict() for agent in self.agents],
            "incidents": [incident.to_dict() for incident in self.incidents],
            "playback": [frame.to_dict() for frame in self.playback],
            "action_requests": [request.to_dict() for request in self.action_requests],
        }
        if include_digest:
            result["scenario_sha256"] = self.scenario_sha256
        return result


def build_checkout_scenario() -> CheckoutScenario:
    """Build the same cross-lens scenario on every call.

    The fixture never upgrades sample observations to production measurements.
    Its revenue value is explicitly MODELED from visible inputs, and its action
    request is structurally unable to execute.
    """

    deployment_ref = "sample:github:run:1842"
    service_ref = "sample:service-window:checkout:20260904t1200z"
    payment_ref = "sample:service-window:payment:20260904t1200z"
    journey_ref = "sample:journey-window:checkout:20260904t1200z"
    agent_ref = "sample:agent-window:support:20260904t1200z"
    model_ref = "model:revenue-at-risk:v1"
    review_ref = "sample:hatun-review:checkout:1"

    burn_formula = error_budget_burn_rate(98_600, 100_000, 0.999)
    revenue_formula = revenue_at_risk(100_000, 0.61, 0.72, 84.0)
    health_formula = journey_health(
        {"cart": 0.98, "payment": 0.61, "confirmation": 0.93},
        {"cart": 0.2, "payment": 0.6, "confirmation": 0.2},
    )

    citations = (
        EvidenceCitation(
            deployment_ref,
            "Sample deployment run 1842",
            "github_actions_fixture",
            "checkout-api deploy at T-15",
            _ANCHOR - timedelta(minutes=15),
            TruthLabel.SAMPLE,
        ),
        EvidenceCitation(
            service_ref,
            "Sample checkout service window",
            "otlp_fixture",
            "100000 requests with 1400 errors",
            _ANCHOR,
            TruthLabel.SAMPLE,
        ),
        EvidenceCitation(
            payment_ref,
            "Sample payment dependency window",
            "otlp_fixture",
            "payment p95 latency 1250 ms",
            _ANCHOR,
            TruthLabel.SAMPLE,
        ),
        EvidenceCitation(
            journey_ref,
            "Sample checkout journey window",
            "governed_event_fixture",
            "completion changed from 0.72 baseline to 0.61",
            _ANCHOR,
            TruthLabel.SAMPLE,
        ),
        EvidenceCitation(
            agent_ref,
            "Sample support agent trace window",
            "otlp_fixture",
            "agent success 0.78 and cost 0.42 USD per trace",
            _ANCHOR,
            TruthLabel.SAMPLE,
        ),
        EvidenceCitation(
            model_ref,
            "Declared revenue-at-risk model",
            "formula_definition",
            revenue_formula.expression,
            _ANCHOR,
            TruthLabel.MODELED,
        ),
        EvidenceCitation(
            review_ref,
            "Sample Hatun review boundary",
            "review_fixture",
            "REVIEW; no execution authority granted",
            _ANCHOR + timedelta(minutes=2),
            TruthLabel.SAMPLE,
        ),
    )

    services = (
        ServiceView(
            service_id="checkout-api",
            name="Checkout API",
            state="DEGRADED",
            indicators={
                "requests": _sample(100_000, service_ref),
                "errors": _sample(1_400, service_ref),
                "availability": _sample(0.986, service_ref),
                "slo_target": _sample(0.999, service_ref),
                "error_budget_burn": _sample(
                    round(float(burn_formula.value), 6),
                    service_ref,
                    metadata={
                        "formula": burn_formula.expression,
                        "unit": "x",
                        "input_truth_labels": ["SAMPLE"],
                    },
                ),
                "p95_latency_ms": _sample(780.0, service_ref),
            },
            dependencies=("payment-api",),
            deployment_refs=(deployment_ref,),
            incident_refs=("incident-checkout-20260904",),
            evidence_refs=(deployment_ref, service_ref, payment_ref),
            truth_label=TruthLabel.SAMPLE,
        ),
        ServiceView(
            service_id="payment-api",
            name="Payment API",
            state="DEGRADED",
            indicators={
                "error_rate": _sample(0.023, payment_ref),
                "p95_latency_ms": _sample(1_250.0, payment_ref),
                "saturation": _sample(0.88, payment_ref),
            },
            incident_refs=("incident-checkout-20260904",),
            evidence_refs=(payment_ref,),
            truth_label=TruthLabel.SAMPLE,
        ),
    )

    steps = (
        JourneyStepView(
            "cart",
            "Cart",
            "HEALTHY",
            ("checkout-api",),
            {"completion_rate": _sample(0.98, journey_ref)},
            (journey_ref,),
            TruthLabel.SAMPLE,
        ),
        JourneyStepView(
            "payment",
            "Payment",
            "DEGRADED",
            ("checkout-api", "payment-api"),
            {
                "completion_rate": _sample(0.61, journey_ref),
                "p95_latency_ms": _sample(1_410.0, journey_ref, payment_ref),
            },
            (journey_ref, payment_ref),
            TruthLabel.SAMPLE,
        ),
        JourneyStepView(
            "confirmation",
            "Confirmation",
            "WATCH",
            ("checkout-api",),
            {"completion_rate": _sample(0.93, journey_ref)},
            (journey_ref,),
            TruthLabel.SAMPLE,
        ),
    )
    journeys = (
        JourneyView(
            journey_id="checkout",
            name="Revenue-critical checkout",
            state="DEGRADED",
            steps=steps,
            indicators={
                "baseline_completion_rate": _sample(0.72, journey_ref),
                "observed_completion_rate": _sample(0.61, journey_ref),
                "abandonment_rate": _sample(0.39, journey_ref),
                "journey_health": _modeled(
                    round(float(health_formula.value), 6),
                    journey_ref,
                    metadata={"formula": health_formula.expression},
                ),
            },
            service_ids=("checkout-api", "payment-api"),
            outcome_ids=("checkout-revenue",),
            impacted_segments=("web customers", "mobile customers"),
            evidence_refs=(journey_ref, service_ref, payment_ref),
            truth_label=TruthLabel.SAMPLE,
        ),
    )
    outcomes = (
        OutcomeView(
            outcome_id="checkout-revenue",
            name="Checkout revenue",
            state="AT_RISK",
            indicators={
                "baseline_conversion_rate": _sample(0.72, journey_ref),
                "observed_conversion_rate": _sample(0.61, journey_ref),
                "average_order_value_usd": _sample(84.0, journey_ref),
                "revenue_at_risk_usd": _modeled(
                    round(float(revenue_formula.value), 2),
                    journey_ref,
                    model_ref,
                    metadata={
                        "formula": revenue_formula.expression,
                        "inputs": dict(revenue_formula.inputs),
                        "can_authorize": False,
                    },
                ),
            },
            service_ids=("checkout-api", "payment-api"),
            journey_ids=("checkout",),
            evidence_refs=(journey_ref, model_ref),
            truth_label=TruthLabel.MODELED,
        ),
    )
    agents = (
        AgentView(
            agent_id="support-copilot",
            name="Support Copilot",
            state="WATCH",
            indicators={
                "trace_count": _sample(2_000, agent_ref),
                "success_rate": _sample(0.78, agent_ref),
                "p95_latency_ms": _sample(4_800.0, agent_ref),
                "cost_per_trace_usd": _sample(0.42, agent_ref),
                "tool_failure_rate": _sample(0.12, agent_ref),
            },
            flags=("cost", "latency", "tool-failure"),
            service_ids=("checkout-api",),
            evidence_refs=(agent_ref,),
            truth_label=TruthLabel.SAMPLE,
        ),
    )
    incidents = (
        IncidentView(
            incident_id="incident-checkout-20260904",
            title="Checkout degradation after sample deployment window",
            state="INVESTIGATING",
            started_at=_ANCHOR,
            resolved_at=None,
            severity="SEV-2",
            service_ids=("checkout-api", "payment-api"),
            journey_ids=("checkout",),
            candidate_factors=(
                IncidentFactor(
                    "factor-deployment-1842",
                    "Sample deployment run 1842 preceded the degraded window by 15 minutes.",
                    "PRECEDED",
                    (deployment_ref, service_ref),
                ),
                IncidentFactor(
                    "factor-payment-latency",
                    "Payment latency and journey abandonment rose in the same sample window.",
                    "CORRELATED",
                    (payment_ref, journey_ref),
                ),
            ),
            evidence_refs=(deployment_ref, service_ref, payment_ref, journey_ref),
            truth_label=TruthLabel.SAMPLE,
        ),
    )
    playback = (
        PlaybackFrame(
            -30,
            _ANCHOR - timedelta(minutes=30),
            "BASELINE",
            ("Checkout completion is at the 0.72 sample baseline.",),
            (journey_ref,),
            TruthLabel.SAMPLE,
        ),
        PlaybackFrame(
            -15,
            _ANCHOR - timedelta(minutes=15),
            "CHANGE",
            ("Sample deployment run 1842 completes for checkout-api.",),
            (deployment_ref,),
            TruthLabel.SAMPLE,
        ),
        PlaybackFrame(
            0,
            _ANCHOR,
            "DEGRADED",
            (
                "Checkout error-budget burn reaches 14x in the fixture.",
                "Journey completion falls to 0.61 and payment p95 reaches 1250 ms.",
            ),
            (service_ref, payment_ref, journey_ref),
            TruthLabel.SAMPLE,
        ),
        PlaybackFrame(
            15,
            _ANCHOR + timedelta(minutes=15),
            "EXPECTED_RECOVERY_FIXTURE",
            (
                "The sample fixture shows recovery after a simulated rollback request.",
                "No production action or causal effect is claimed.",
            ),
            (review_ref,),
            TruthLabel.SAMPLE,
        ),
    )
    action_requests = (
        ActionRequestView(
            request_id="sample-rollback-request-1",
            action_type="rollback",
            state="NOT_EXECUTED",
            simulation=True,
            can_execute=False,
            evidence_refs=(deployment_ref, service_ref, review_ref),
            truth_label=TruthLabel.SAMPLE,
        ),
    )
    return CheckoutScenario(
        scenario_id=SAMPLE_SCENARIO_ID,
        generated_at=_ANCHOR,
        citations=citations,
        services=services,
        journeys=journeys,
        outcomes=outcomes,
        agents=agents,
        incidents=incidents,
        playback=playback,
        action_requests=action_requests,
    )


sample_checkout_scenario = build_checkout_scenario

__all__ = [
    "SAMPLE_SCENARIO_ID",
    "CheckoutScenario",
    "build_checkout_scenario",
    "sample_checkout_scenario",
]
