"""Living Anatomy, formulas, and enterprise positioning contracts."""
from __future__ import annotations

ANATOMY = (
    ("sense", "Acquire fixed public telemetry or explicit caller observations."),
    ("normalize", "Validate counts, units, identifiers, timing, cost, and source."),
    ("context", "Bind services, traces, outcomes, source revision, and session."),
    ("formula", "Calculate SLOs, burn, latency, cost, outcomes, and advisory Lambda."),
    ("policy", "Deny effectors and unsupported causality or authority claims."),
    ("decide", "Rank a human-owned investigation queue or abstain."),
    ("verify", "Check source identity, receipts, invariants, and freshness."),
    ("remember", "Store bounded summaries under a hashed caller session."),
    ("receipt", "Mint deterministic analysis and source receipts."),
)

FORMULAS = (
    {
        "id": "lyte.availability_sli",
        "equation": "SLI = good_events / total_events",
        "status": "TESTED_IMPLEMENTATION",
        "can_authorize": False,
    },
    {
        "id": "lyte.error_budget_burn",
        "equation": "burn = observed_bad_rate / allowed_bad_rate",
        "status": "TESTED_IMPLEMENTATION",
        "can_authorize": False,
    },
    {
        "id": "lyte.percentile",
        "equation": "linear interpolation over ordered latency samples",
        "status": "TESTED_IMPLEMENTATION",
        "can_authorize": False,
    },
    {
        "id": "lyte.cost_per_success",
        "equation": "cost_usd / successful_outcomes",
        "status": "TESTED_IMPLEMENTATION",
        "can_authorize": False,
    },
    {
        "id": "lyte.outcome_attainment",
        "equation": "higher: current/target; lower: target/current; clamp [0,1]",
        "status": "MODELED",
        "can_authorize": False,
    },
    {
        "id": "lyte.journey_gap_value",
        "equation": "(target_success_rate - observed_rate)+ × volume × value_per_success",
        "status": "MODELED",
        "can_authorize": False,
    },
    {
        "id": "szl.lambda_advisory",
        "equation": "Λ_w(x)=∏xᵢ^wᵢ, Σwᵢ=1",
        "status": "CONJECTURE_1_ADVISORY",
        "can_authorize": False,
    },
    {
        "id": "szl.receipt_hash",
        "equation": "r = SHA-256(canonical_json(metadata))",
        "status": "DETERMINISTIC_IMPLEMENTATION",
        "can_authorize": False,
    },
)

POSITIONING = {
    "category": "GOVERNED_ENTERPRISE_OBSERVABILITY",
    "promise": (
        "Connect technical telemetry, AI-agent operations, business journeys, "
        "economic impact, and decision evidence in one operating state."
    ),
    "wedge": "BUSINESS_CRITICAL_JOURNEYS_PLUS_AI_OPERATIONS_PLUS_PROOF",
    "deployment_motion": "CONNECT_EXISTING_STACKS_NOT_RIP_AND_REPLACE",
    "primary_icp": {
        "company_profile": (
            "Digitally dependent upper-mid-market and enterprise organizations "
            "with multi-team service estates, material incident cost, AI workloads, "
            "or regulated decision evidence requirements."
        ),
        "buyers": [
            "CIO / CTO",
            "VP Engineering / Platform",
            "Head of SRE / IT Operations",
            "Head of AI Platform",
            "Operations / Transformation leader",
        ],
        "operators": [
            "SRE",
            "ITOps",
            "Platform Engineering",
            "Application Engineering",
            "AI Engineering",
            "FinOps",
            "Product Operations",
        ],
        "expansion": [
            "Customer support",
            "Product leadership",
            "Finance",
            "Risk and compliance",
            "Executive operations",
        ],
    },
    "best_initial_industries": [
        "Financial services and insurance",
        "Healthcare and life sciences",
        "Retail and digital commerce",
        "Logistics and manufacturing",
        "B2B SaaS and AI-native companies",
    ],
    "competitive_boundary": {
        "does": [
            "Consume OpenTelemetry-shaped observations and existing tool data",
            "Connect service state to declared journeys and business impact",
            "Observe AI agents without retaining prompt or response content",
            "Preserve source and decision receipts",
            "Rank human-owned investigations",
        ],
        "does_not": [
            "Claim causal proof from correlation",
            "Replace every telemetry collector on day one",
            "Execute unattended remediations",
            "Treat Lambda as an authorization theorem",
        ],
    },
}


