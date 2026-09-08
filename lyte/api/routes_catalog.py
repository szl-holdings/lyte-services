"""Static, source-owned catalogs for the public product contract."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from fastapi import APIRouter, Request

from lyte.domain import TruthLabel

router = APIRouter(prefix="/api/lyte/v2", tags=["catalog"])

LENSES = (
    ("service", "Service", "Reliability, SLOs, dependencies, deployments, and recovery."),
    ("journey", "Journey", "Completion, abandonment, latency, segments, and dependencies."),
    ("business", "Business", "Revenue, cost, service impact, target attainment, and risk."),
    ("agent", "AI Agent", "Success, latency, tools, tokens, cost, quality, and escalation."),
    ("delivery", "Delivery", "Deployment health, lead time, change failure, and queue pressure."),
    ("decision", "Decision", "Evidence, recommendation, owner, review, verification, and receipt."),
)

ANATOMY = (
    ("sense", "Acquire an allowlisted source or explicit caller observation."),
    ("normalize", "Validate schema, limits, identifiers, units, currency, and time."),
    ("context", "Bind tenant, workspace, source, entities, journey, and outcome."),
    ("formula", "Calculate reliability and economic results with explicit availability."),
    ("policy", "Deny effectors, unsupported authority, and unsupported causality."),
    ("decide", "Return REVIEW, ABSTAIN, or DENY for a human owner."),
    ("verify", "Verify evidence, invariants, receipt chain, and source freshness."),
    ("remember", "Persist scoped summaries and evidence references, never raw tokens."),
    ("receipt", "Append a finite canonical record to the scoped hash chain."),
)

FORMULAS = (
    ("lyte.availability_sli", "good_events / total_events", "MEASURED"),
    ("lyte.error_rate", "bad_events / total_events", "MEASURED"),
    ("lyte.error_budget_burn", "error_rate / (1 - slo_target)", "MEASURED"),
    ("lyte.error_budget_remaining", "max(0, 1 - error_budget_burn)", "MEASURED"),
    ("lyte.requests_per_second", "requests / window_seconds", "MEASURED"),
    ("lyte.change_failure_rate", "failed_changes / total_changes", "MEASURED"),
    ("lyte.mean_time_to_recovery", "sum(recovery_duration) / recovered_incidents", "MEASURED"),
    ("lyte.apdex", "(satisfied + 0.5 * tolerated) / total", "MEASURED"),
    ("lyte.cost_per_success", "cost / successful_outcomes", "MEASURED"),
    ("lyte.outcome_attainment", "directional current / target, clamped [0,1]", "MODELED"),
    ("lyte.revenue_at_risk", "explicit reported or modeled value", "MODELED"),
    ("lyte.journey_health", "declared weighted composition of step health", "MODELED"),
    ("szl.lambda_advisory", "bounded weighted geometric composition", "CONJECTURE_1_ADVISORY"),
    ("szl.receipt_id", "SHA-256(canonical_json(receipt_basis))", "DETERMINISTIC"),
)


def _formula_rows() -> list[dict[str, Any]]:
    return [
        {
            "id": formula_id,
            "equation": equation,
            "proof_status": status,
            "can_authorize": False,
            "can_be_sole_allow_basis": False,
        }
        for formula_id, equation, status in FORMULAS
    ]


@router.get("/catalog")
def catalog(request: Request) -> dict[str, Any]:
    runtime = request.app.state.runtime
    return {
        "schema": "szl.lyte-catalog/v2",
        "product": "Lyte Enterprise Signal Lattice",
        "positioning": "Governed business observability command system",
        "lenses": [
            {"id": lens_id, "name": name, "description": description}
            for lens_id, name, description in LENSES
        ],
        "scenes": [
            "Executive Command",
            "Service Intelligence",
            "Journey Intelligence",
            "AI Agent Operations",
            "Incident Playback",
        ],
        "data_mode": "SAMPLE" if runtime.demo_mode else "REAL_ONLY",
        "effectors_enabled": False,
        "truth_label": "REPORTED",
    }


@router.get("/capabilities")
def capabilities(request: Request) -> dict[str, Any]:
    runtime = request.app.state.runtime
    return {
        "schema": "szl.lyte-capabilities/v2",
        "operational": [
            "sqlite_demo_persistence",
            "postgresql_production_boundary",
            "tenant_workspace_isolation",
            "github_actions_read_only",
            "otlp_json_subset_ingest",
            "governed_event_webhook",
            "living_anatomy",
            "deterministic_ask_lyte",
            "second_brain",
            "hatun_review",
            "prometheus_metrics",
            "source_identity",
        ],
        "roadmap": [
            "prometheus_remote_read",
            "azure_monitor",
            "aws_cloudwatch",
            "kubernetes",
            "servicenow",
            "jira",
            "salesforce",
            "cloud_cost_provider_adapters",
        ],
        "authentication": runtime.authentication_state,
        "effectors_enabled": False,
        "truth_label": "MEASURED",
    }


@router.get("/anatomy")
def anatomy() -> dict[str, Any]:
    return {
        "schema": "szl.lyte-anatomy-catalog/v2",
        "stages": [
            {"sequence": sequence, "stage": stage, "description": description}
            for sequence, (stage, description) in enumerate(ANATOMY, start=1)
        ],
        "machine_enforced": True,
        "effectors_enabled": False,
        "truth_label": TruthLabel.REPORTED.value,
    }


@router.get("/formulas")
def formulas() -> dict[str, Any]:
    return {
        "schema": "szl.lyte-formula-catalog/v2",
        "formulas": _formula_rows(),
        "lambda_status": "CONJECTURE_1_ADVISORY",
        "formula_output_can_authorize": False,
        "truth_label": "REPORTED",
    }


@router.get("/sources")
def sources(request: Request) -> dict[str, Any]:
    runtime = request.app.state.runtime
    connectors = runtime.connector_catalog()
    normalized = [
        asdict(row) if is_dataclass(row) else dict(row) if isinstance(row, dict) else row
        for row in connectors
    ]
    return {
        "schema": "szl.lyte-source-catalog/v2",
        "sources": normalized,
        "arbitrary_url_fetch": False,
        "truth_label": "MEASURED",
    }
