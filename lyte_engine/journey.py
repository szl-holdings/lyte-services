"""Declared business-journey analysis and impact modeling."""
from __future__ import annotations

from typing import Any, Mapping

from .core import clamp01, finite_float, weighted_geometric_mean

def analyze_journey(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Analyze a declared business journey and quantify stage-level impact."""
    name = " ".join(str(payload.get("name", "")).split())
    stages = payload.get("stages", [])
    if not name:
        raise ValueError("journey name is required")
    if not isinstance(stages, list) or not stages:
        raise ValueError("at least one journey stage is required")
    if len(stages) > 50:
        raise ValueError("journey exceeds 50 stages")

    rows: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    total_revenue_risk = 0.0
    stage_scores: dict[str, float] = {}
    stage_weights: dict[str, float] = {}

    for index, raw in enumerate(stages):
        if not isinstance(raw, Mapping):
            raise ValueError("each journey stage must be an object")
        stage_name = " ".join(str(raw.get("name", "")).split())
        if not stage_name:
            raise ValueError("journey stage name is required")
        if stage_name in stage_scores:
            raise ValueError("journey stage names must be unique")
        volume = int(raw.get("volume", 0))
        if volume < 0:
            raise ValueError("journey volume must be non-negative")
        success_rate = clamp01(float(raw.get("success_rate", 0.0)))
        target_rate = clamp01(float(raw.get("target_success_rate", 0.0)))
        if target_rate <= 0:
            raise ValueError("target_success_rate must be greater than zero")
        value_per_success = finite_float(
            raw.get("value_per_success_usd", 0.0),
            name="value_per_success_usd",
            minimum=0.0,
        )
        gap = max(0.0, target_rate - success_rate)
        missed = gap * volume
        revenue_risk = missed * value_per_success
        total_revenue_risk += revenue_risk
        attainment = clamp01(success_rate / target_rate)
        weight = clamp01(float(raw.get("weight", 1.0)))
        stage_scores[stage_name] = attainment
        stage_weights[stage_name] = weight
        state = "HEALTHY" if attainment >= 0.95 else "WATCH" if attainment >= 0.80 else "CRITICAL"
        rows.append(
            {
                "name": stage_name,
                "service": " ".join(str(raw.get("service", "")).split()) or None,
                "volume": volume,
                "success_rate": round(success_rate, 8),
                "target_success_rate": round(target_rate, 8),
                "attainment": round(attainment, 6),
                "missed_successes": round(missed, 3),
                "value_per_success_usd": round(value_per_success, 2),
                "revenue_at_risk_usd": round(revenue_risk, 2),
                "weight": weight,
                "state": state,
                "truth_label": "MODELED",
            }
        )
        if index:
            edges.append(
                {
                    "from": rows[index - 1]["name"],
                    "to": stage_name,
                    "relation": "DECLARED_JOURNEY_SEQUENCE",
                    "causality_claimed": False,
                }
            )

    journey_lambda = weighted_geometric_mean(stage_scores, stage_weights)
    return {
        "schema": "szl.lyte-journey/v1",
        "name": name,
        "stages": rows,
        "graph": {
            "edges": edges,
            "edge_count": len(edges),
            "causality_claimed": False,
        },
        "journey_health": journey_lambda,
        "revenue_at_risk_usd": round(total_revenue_risk, 2),
        "status": (
            "HEALTHY"
            if journey_lambda["score"] >= 0.90
            else "WATCH"
            if journey_lambda["score"] >= 0.70
            else "CRITICAL"
        ),
        "effectors_enabled": False,
        "human_approval_required": True,
        "truth_label": "MODELED",
    }


