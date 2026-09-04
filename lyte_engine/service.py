"""Service, AI-agent, six-lens, and enterprise-state analysis."""
from __future__ import annotations

import hashlib
import math
from statistics import fmean
from typing import Any, Mapping, Sequence

from .core import (
    LENSES,
    VERSION,
    clamp01,
    cost_per_success,
    error_budget,
    finite_float,
    outcome_attainment,
    percentile,
    weighted_geometric_mean,
)

def agent_trace_summary(traces: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate AI-agent traces without retaining prompts or responses."""
    if not traces:
        return {
            "trace_count": 0,
            "successful_traces": 0,
            "success_rate": None,
            "p50_latency_ms": None,
            "p95_latency_ms": None,
            "total_cost_usd": 0.0,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "privacy_flags": 0,
            "safety_flags": 0,
            "tool_calls": 0,
            "truth_label": "UNAVAILABLE",
        }
    latency: list[float] = []
    total_cost = 0.0
    total_input = 0
    total_output = 0
    successes = 0
    privacy_flags = 0
    safety_flags = 0
    tool_calls = 0
    for trace in traces:
        latency.append(
            finite_float(trace.get("latency_ms", 0.0), name="trace.latency_ms", minimum=0.0)
        )
        total_cost += finite_float(
            trace.get("cost_usd", 0.0), name="trace.cost_usd", minimum=0.0
        )
        total_input += max(0, int(trace.get("input_tokens", 0)))
        total_output += max(0, int(trace.get("output_tokens", 0)))
        successes += int(bool(trace.get("success", False)))
        privacy_flags += int(bool(trace.get("privacy_flag", False)))
        safety_flags += int(bool(trace.get("safety_flag", False)))
        tool_calls += max(0, int(trace.get("tool_calls", 0)))
    return {
        "trace_count": len(traces),
        "successful_traces": successes,
        "success_rate": round(successes / len(traces), 8),
        "p50_latency_ms": round(percentile(latency, 0.50) or 0.0, 3),
        "p95_latency_ms": round(percentile(latency, 0.95) or 0.0, 3),
        "total_cost_usd": round(total_cost, 6),
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "privacy_flags": privacy_flags,
        "safety_flags": safety_flags,
        "tool_calls": tool_calls,
        "prompt_content_recorded": False,
        "response_content_recorded": False,
        "truth_label": "MEASURED",
    }


def _service_priority(
    *,
    burn_rate: float | None,
    revenue_at_risk_usd: float,
    customer_impact: float,
    severity: float = 0.0,
) -> float:
    burn_component = min(4.0, max(0.0, burn_rate or 0.0)) / 4.0
    impact = clamp01(customer_impact)
    revenue = clamp01(math.log10(1.0 + max(0.0, revenue_at_risk_usd)) / 8.0)
    severity_component = clamp01(severity)
    return round(
        0.38 * burn_component
        + 0.28 * impact
        + 0.19 * revenue
        + 0.15 * severity_component,
        6,
    )


def _trend_score(values: Sequence[float], *, higher_is_better: bool) -> dict[str, Any]:
    clean = [finite_float(value, name="trend sample") for value in values]
    if len(clean) < 2:
        return {
            "direction": "UNAVAILABLE",
            "change": None,
            "score": 0.5,
            "truth_label": "UNAVAILABLE",
        }
    first = clean[0]
    last = clean[-1]
    change = last - first
    scale = max(abs(first), abs(last), 1.0)
    signed = change / scale
    improvement = signed if higher_is_better else -signed
    score = clamp01(0.5 + improvement / 2.0)
    direction = "IMPROVING" if improvement > 0.02 else "DEGRADING" if improvement < -0.02 else "STABLE"
    return {
        "direction": direction,
        "change": round(change, 8),
        "score": round(score, 6),
        "truth_label": "MODELED",
    }


def derive_lenses(
    *,
    services: Sequence[Mapping[str, Any]],
    outcomes: Sequence[Mapping[str, Any]],
    trace_summary: Mapping[str, Any],
    graph_edges: Sequence[Mapping[str, Any]],
    action_queue: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Derive the six proprietary Lyte lenses from explicit measurements."""
    service_scores = [
        float(item.get("lambda_advisory", {}).get("score", 0.0))
        for item in services
    ]
    posture = fmean(service_scores) if service_scores else 0.0
    revenue_at_risk = sum(
        float(item.get("business", {}).get("revenue_at_risk_usd", 0.0))
        for item in services
    )
    outcome_scores = [float(item.get("attainment", 0.0)) for item in outcomes]
    impact_score = fmean(outcome_scores) if outcome_scores else 0.5
    queue_pressure = min(1.0, len(action_queue) / max(1, len(services)))
    signal_score = 1.0 - queue_pressure
    topology_score = min(1.0, len(graph_edges) / max(1, len(services) * 2))
    trace_success = trace_summary.get("success_rate")
    anticipation_score = (
        clamp01(float(trace_success)) if isinstance(trace_success, (int, float)) else 0.5
    )
    burn_series = [
        max(0.0, 1.0 - min(4.0, float(item.get("slo", {}).get("burn_rate") or 0.0)) / 4.0)
        for item in services
    ]
    velocity_score = fmean(burn_series) if burn_series else 0.5

    raw = {
        "signal": signal_score,
        "impact": impact_score,
        "anticipation": anticipation_score,
        "topology": topology_score,
        "posture": posture,
        "velocity": velocity_score,
    }
    metadata = {
        "signal": {
            "top_signal": action_queue[0]["reason"] if action_queue else "No active queue item",
            "queue_items": len(action_queue),
        },
        "impact": {
            "revenue_at_risk_usd": round(revenue_at_risk, 2),
            "outcomes": len(outcomes),
        },
        "anticipation": {
            "basis": "observed agent success and explicit trends",
            "forecast_claimed": False,
        },
        "topology": {
            "edges": len(graph_edges),
            "causality_claimed": False,
        },
        "posture": {
            "service_count": len(services),
            "axes_exposed": True,
        },
        "velocity": {
            "basis": "current burn headroom; historical trend requires playback",
        },
    }
    lenses: list[dict[str, Any]] = []
    definitions = {item["id"]: item for item in LENSES}
    for lens_id, score in raw.items():
        bounded = clamp01(score)
        status = "HEALTHY" if bounded >= 0.80 else "WATCH" if bounded >= 0.55 else "CRITICAL"
        lenses.append(
            {
                **definitions[lens_id],
                "score": round(bounded, 6),
                "status": status,
                "metadata": metadata[lens_id],
                "truth_label": "MODELED",
            }
        )
    return lenses


def analyze_window(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Build a service-to-outcome operating envelope."""
    services = payload.get("services", [])
    outcomes = payload.get("outcomes", [])
    traces = payload.get("traces", [])
    if not isinstance(services, list) or not services:
        raise ValueError("at least one service observation is required")
    if not isinstance(outcomes, list):
        raise ValueError("outcomes must be a list")
    if not isinstance(traces, list):
        raise ValueError("traces must be a list")

    outcome_rows: list[dict[str, Any]] = []
    outcome_scores: dict[str, float] = {}
    outcome_weights: dict[str, float] = {}
    for item in outcomes:
        if not isinstance(item, Mapping):
            raise ValueError("each outcome must be an object")
        name = " ".join(str(item.get("name", "")).split())
        if not name:
            raise ValueError("outcome name is required")
        if name in outcome_scores:
            raise ValueError("outcome names must be unique")
        score = outcome_attainment(
            current=float(item.get("current", 0.0)),
            target=float(item.get("target", 0.0)),
            direction=str(item.get("direction", "")),
        )
        weight = clamp01(float(item.get("weight", 1.0)))
        outcome_scores[name] = score
        outcome_weights[name] = weight
        outcome_rows.append(
            {
                "name": name,
                "current": float(item["current"]),
                "target": float(item["target"]),
                "direction": str(item["direction"]).lower(),
                "weight": weight,
                "attainment": round(score, 6),
                "truth_label": "MEASURED",
            }
        )

    trace_summary = agent_trace_summary(
        [item for item in traces if isinstance(item, Mapping)]
    )
    service_rows: list[dict[str, Any]] = []
    action_queue: list[dict[str, Any]] = []
    graph_edges: list[dict[str, Any]] = []

    for item in services:
        if not isinstance(item, Mapping):
            raise ValueError("each service must be an object")
        name = " ".join(str(item.get("name", "")).split())
        if not name:
            raise ValueError("service name is required")
        good = int(item.get("good_events", 0))
        total = int(item.get("total_events", 0))
        if good > total:
            raise ValueError("good_events cannot exceed total_events")
        budget = error_budget(
            good_events=good,
            total_events=total,
            target=float(item.get("slo_target", 0.99)),
        )
        latency_values = [
            finite_float(value, name=f"{name}.latency_ms", minimum=0.0)
            for value in item.get("latency_ms", [])
        ]
        cost = finite_float(item.get("cost_usd", 0.0), name=f"{name}.cost_usd", minimum=0.0)
        successful = int(item.get("successful_outcomes", good))
        revenue_at_risk = finite_float(
            item.get("revenue_at_risk_usd", 0.0),
            name=f"{name}.revenue_at_risk_usd",
            minimum=0.0,
        )
        customer_impact = clamp01(float(item.get("customer_impact", 0.0)))
        severity = clamp01(float(item.get("severity", customer_impact)))
        dependencies = sorted(
            {
                " ".join(str(value).split())
                for value in item.get("dependencies", [])
                if " ".join(str(value).split())
            }
        )
        linked_outcomes = [
            str(value)
            for value in item.get("outcome_names", [])
            if str(value) in outcome_scores
        ]
        if linked_outcomes:
            linked_weight_total = sum(outcome_weights[name] for name in linked_outcomes)
            if linked_weight_total > 0:
                linked_score = sum(
                    outcome_scores[name] * outcome_weights[name]
                    for name in linked_outcomes
                ) / linked_weight_total
            else:
                linked_score = fmean(outcome_scores[name] for name in linked_outcomes)
        else:
            linked_score = 0.5

        sli = budget["sli"]
        reliability = float(sli) if isinstance(sli, (int, float)) else 0.0
        unit_cost = cost_per_success(cost, successful)
        cost_budget = finite_float(
            item.get("cost_budget_per_success_usd", 100.0),
            name=f"{name}.cost_budget_per_success_usd",
            minimum=0.0,
        )
        if cost_budget <= 0:
            raise ValueError("cost_budget_per_success_usd must be greater than zero")
        cost_score = clamp01(
            1.0 - (unit_cost if unit_cost is not None else cost) / cost_budget
        )
        service_lambda = weighted_geometric_mean(
            {
                "reliability": reliability,
                "outcome_attainment": linked_score,
                "cost_efficiency": cost_score,
                "customer_safety": 1.0 - customer_impact,
                "evidence_quality": clamp01(float(item.get("evidence_quality", 0.85))),
            },
            {
                "reliability": 0.30,
                "outcome_attainment": 0.24,
                "cost_efficiency": 0.14,
                "customer_safety": 0.20,
                "evidence_quality": 0.12,
            },
        )
        priority = _service_priority(
            burn_rate=budget["burn_rate"],
            revenue_at_risk_usd=revenue_at_risk,
            customer_impact=customer_impact,
            severity=severity,
        )
        p50 = percentile(latency_values, 0.50)
        p95 = percentile(latency_values, 0.95)
        p99 = percentile(latency_values, 0.99)
        trend = _trend_score(
            [float(value) for value in item.get("health_trend", [])],
            higher_is_better=True,
        )
        row = {
            "name": name,
            "slo": budget,
            "latency": {
                "p50_ms": round(p50, 3) if p50 is not None else None,
                "p95_ms": round(p95, 3) if p95 is not None else None,
                "p99_ms": round(p99, 3) if p99 is not None else None,
                "sample_count": len(latency_values),
                "truth_label": "MEASURED" if latency_values else "UNAVAILABLE",
            },
            "cost": {
                "total_usd": round(cost, 6),
                "successful_outcomes": successful,
                "per_success_usd": round(unit_cost, 6) if unit_cost is not None else None,
                "budget_per_success_usd": round(cost_budget, 6),
                "truth_label": "MEASURED",
            },
            "business": {
                "outcome_names": linked_outcomes,
                "outcome_attainment": round(linked_score, 6),
                "revenue_at_risk_usd": round(revenue_at_risk, 2),
                "customer_impact": round(customer_impact, 6),
                "causality_claimed": False,
            },
            "dependencies": dependencies,
            "trend": trend,
            "lambda_advisory": service_lambda,
            "priority": priority,
            "truth_label": "MEASURED",
        }
        service_rows.append(row)

        if budget["state"] != "HEALTHY" or priority >= 0.35:
            action_queue.append(
                {
                    "service": name,
                    "priority": priority,
                    "reason": (
                        f"SLO {budget['state']} · burn {budget['burn_rate']} · "
                        f"revenue at risk ${revenue_at_risk:.2f}"
                    ),
                    "recommended_action": "INVESTIGATE_WITH_HUMAN_OWNER",
                    "can_execute": False,
                    "human_approval_required": True,
                    "truth_label": "MODELED",
                }
            )
        for dependency in dependencies:
            graph_edges.append(
                {
                    "from": dependency,
                    "to": name,
                    "relation": "SERVICE_DEPENDENCY",
                    "causality_claimed": False,
                }
            )
        for outcome_name in linked_outcomes:
            graph_edges.append(
                {
                    "from": name,
                    "to": outcome_name,
                    "relation": "DECLARED_OUTCOME_LINK",
                    "causality_claimed": False,
                }
            )

    action_queue.sort(key=lambda item: (-item["priority"], item["service"]))
    service_health = fmean(
        float(row["lambda_advisory"]["score"]) for row in service_rows
    )
    if outcome_scores:
        weight_total = sum(outcome_weights.values())
        outcome_health = (
            sum(outcome_scores[name] * outcome_weights[name] for name in outcome_scores)
            / weight_total
            if weight_total > 0
            else fmean(outcome_scores.values())
        )
    else:
        outcome_health = 0.5
    trace_success = trace_summary["success_rate"]
    agent_health = float(trace_success) if isinstance(trace_success, (int, float)) else 0.5
    privacy_safety = (
        1.0
        if not traces
        else 1.0
        - (
            trace_summary["privacy_flags"] + trace_summary["safety_flags"]
        )
        / (2.0 * max(1, trace_summary["trace_count"]))
    )
    portfolio_lambda = weighted_geometric_mean(
        {
            "service_health": service_health,
            "outcome_health": outcome_health,
            "agent_health": agent_health,
            "privacy_safety": clamp01(privacy_safety),
        },
        {
            "service_health": 0.38,
            "outcome_health": 0.30,
            "agent_health": 0.18,
            "privacy_safety": 0.14,
        },
    )

    evidence_refs = [
        " ".join(str(value).split())
        for value in payload.get("evidence_refs", [])
        if " ".join(str(value).split())
    ]
    evidence_digests = [
        hashlib.sha256(value.encode("utf-8")).hexdigest() for value in evidence_refs
    ]
    lenses = derive_lenses(
        services=service_rows,
        outcomes=outcome_rows,
        trace_summary=trace_summary,
        graph_edges=graph_edges,
        action_queue=action_queue,
    )
    total_revenue_risk = sum(
        float(row["business"]["revenue_at_risk_usd"]) for row in service_rows
    )
    return {
        "schema": "szl.lyte-window/v3",
        "version": VERSION,
        "services": service_rows,
        "outcomes": outcome_rows,
        "agent_traces": trace_summary,
        "service_outcome_graph": {
            "edges": graph_edges,
            "edge_count": len(graph_edges),
            "causality_claimed": False,
        },
        "action_queue": action_queue,
        "lenses": lenses,
        "enterprise_state": {
            "posture_score": portfolio_lambda["score"],
            "status": (
                "HEALTHY"
                if portfolio_lambda["score"] >= 0.80
                else "WATCH"
                if portfolio_lambda["score"] >= 0.55
                else "CRITICAL"
            ),
            "revenue_at_risk_usd": round(total_revenue_risk, 2),
            "services_observed": len(service_rows),
            "outcomes_observed": len(outcome_rows),
            "queue_items": len(action_queue),
        },
        "portfolio_lambda": portfolio_lambda,
        "evidence_ref_sha256": evidence_digests,
        "raw_evidence_refs_recorded": False,
        "causality_claimed": False,
        "effectors_enabled": False,
        "human_approval_required": True,
        "truth_label": "MODELED",
    }


