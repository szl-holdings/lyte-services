"""Lyte enterprise business-observability runtime.

Lyte connects technical reliability, customer journeys, AI-agent behavior,
cost, and explicitly declared business outcomes. It is evidence-first:
correlation is never silently promoted to causality, public sources are fixed,
and every analysis or observation receives a deterministic receipt.

The runtime has no autonomous remediation path. Hatun returns REVIEW or
ABSTAIN, and all suggested actions remain human-owned.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
import re
import threading
import time
from collections import Counter, OrderedDict, defaultdict
from statistics import fmean
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx

VERSION = "3.0.0"
SOURCE_REPOSITORY = "szl-holdings/lyte-services"
TRUST_CEILING = 0.97
MAX_MEMORY_PAYLOAD_BYTES = 256_000

SESSION_TOKEN = re.compile(r"^[A-Za-z0-9._~-]{32,128}$")
REPOSITORY = re.compile(r"^[a-z0-9][a-z0-9._-]{0,99}$")
RECEIPT_ID = re.compile(r"^[0-9a-f]{64}$")

GITHUB_REPOSITORIES = {
    "a11oy",
    "anatomy",
    "david-leads",
    "hatun-mcp",
    "immune",
    "killinchu",
    "lyte-lattice",
    "lyte-services",
    "platform",
    "puriq-live",
    "szl-defensive-control-plane",
    "szl-formulas",
    "szl-real-estate",
    "szl-second-brain",
    "vertical-services",
}
USER_AGENT = "SZL-Lyte-Enterprise/3.0 (+https://a-11-oy.com)"

EVENT_CLASSES = (
    "business.transaction.started",
    "business.transaction.completed",
    "business.transaction.failed",
    "business.risk.detected",
    "business.opportunity.created",
    "policy.violation.detected",
    "recommendation.generated",
    "action.approved",
    "action.executed",
    "action.failed",
    "outcome.realized",
)
SEVERITIES = ("info", "low", "medium", "high", "critical")

LENSES: tuple[dict[str, Any], ...] = (
    {
        "id": "signal",
        "name": "Signal",
        "tagline": "See what matters now.",
        "purpose": "Rank the few observations that demand attention and preserve their evidence.",
    },
    {
        "id": "impact",
        "name": "Impact",
        "tagline": "Every event has a consequence.",
        "purpose": "Translate declared operational effects into revenue, cost, service, and risk context.",
    },
    {
        "id": "anticipation",
        "name": "Anticipation",
        "tagline": "Project, do not pretend.",
        "purpose": "Estimate trajectory from explicit trends without presenting forecasts as guarantees.",
    },
    {
        "id": "topology",
        "name": "Topology",
        "tagline": "Show the dependency field.",
        "purpose": "Reveal declared and observed service, trace, journey, and outcome relationships.",
    },
    {
        "id": "posture",
        "name": "Posture",
        "tagline": "One bounded operating score.",
        "purpose": "Summarize health while exposing every contributing axis and proof boundary.",
    },
    {
        "id": "velocity",
        "name": "Velocity",
        "tagline": "Measure improvement rate.",
        "purpose": "Track delivery, recovery, journey, and learning movement over time.",
    },
)


class SourceUnavailable(RuntimeError):
    """A fixed public source did not produce a valid observation."""


def canonical_json(value: Any) -> str:
    """Serialize a JSON-compatible value deterministically."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def clamp01(value: float) -> float:
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError("value must be finite")
    return min(1.0, max(0.0, numeric))


def finite_float(value: Any, *, name: str, minimum: float | None = None) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not math.isfinite(numeric):
        raise ValueError(f"{name} must be finite")
    if minimum is not None and numeric < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return numeric


def percentile(values: Iterable[float], quantile: float) -> float | None:
    """Return a linearly interpolated percentile for finite samples."""
    clean: list[float] = []
    for raw in values:
        numeric = finite_float(raw, name="sample")
        clean.append(numeric)
    if not clean:
        return None
    q = clamp01(quantile)
    ordered = sorted(clean)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def availability_sli(good_events: int, total_events: int) -> float | None:
    total = int(total_events)
    good = int(good_events)
    if total <= 0:
        return None
    if good < 0 or good > total:
        raise ValueError("good_events must be between zero and total_events")
    return good / total


def error_budget(
    *,
    good_events: int,
    total_events: int,
    target: float,
) -> dict[str, Any]:
    """Return a count-based SLI, error-budget burn, and operating state."""
    target_value = clamp01(target)
    if target_value >= 1.0:
        raise ValueError("SLO target must be below 1.0")
    sli = availability_sli(good_events, total_events)
    if sli is None:
        return {
            "sli": None,
            "target": target_value,
            "allowed_bad_rate": round(1.0 - target_value, 8),
            "observed_bad_rate": None,
            "burn_rate": None,
            "error_budget_remaining": None,
            "state": "UNAVAILABLE",
            "truth_label": "UNAVAILABLE",
        }
    allowed_bad_rate = 1.0 - target_value
    observed_bad_rate = 1.0 - sli
    burn = observed_bad_rate / allowed_bad_rate
    if sli >= target_value and burn <= 1.0:
        state = "HEALTHY"
    elif burn <= 2.0:
        state = "WATCH"
    else:
        state = "BREACHED"
    return {
        "sli": round(sli, 8),
        "target": round(target_value, 8),
        "allowed_bad_rate": round(allowed_bad_rate, 8),
        "observed_bad_rate": round(observed_bad_rate, 8),
        "burn_rate": round(burn, 6),
        "error_budget_remaining": round(max(0.0, 1.0 - burn), 6),
        "state": state,
        "truth_label": "MEASURED",
    }


def outcome_attainment(
    *,
    current: float,
    target: float,
    direction: str,
) -> float:
    current_value = finite_float(current, name="current")
    target_value = finite_float(target, name="target", minimum=0.0)
    if target_value <= 0:
        raise ValueError("outcome target must be greater than zero")
    direction_value = direction.strip().lower()
    if direction_value == "higher":
        score = current_value / target_value
    elif direction_value == "lower":
        score = target_value / max(current_value, 1e-12)
    else:
        raise ValueError("direction must be higher or lower")
    return clamp01(score)


def weighted_geometric_mean(
    axes: Mapping[str, float],
    weights: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Apply the SZL Lambda-shaped advisory with explicit axes and weights.

    This is a deterministic implementation of a weighted geometric mean.
    Lambda uniqueness remains Conjecture 1 and the score cannot authorize.
    """
    if not axes:
        raise ValueError("at least one axis is required")
    clean = {str(name): clamp01(float(value)) for name, value in axes.items()}
    if weights is None:
        normalized_weights = {name: 1.0 / len(clean) for name in clean}
    else:
        if set(weights) != set(clean):
            raise ValueError("weights must name the same axes")
        raw_weights = {
            name: finite_float(weights[name], name=f"weight.{name}", minimum=0.0)
            for name in clean
        }
        total_weight = sum(raw_weights.values())
        if total_weight <= 0:
            raise ValueError("weight sum must be positive")
        normalized_weights = {
            name: raw_weights[name] / total_weight for name in clean
        }

    if any(value == 0.0 and normalized_weights[name] > 0 for name, value in clean.items()):
        score = 0.0
    else:
        score = math.exp(
            sum(
                normalized_weights[name] * math.log(max(value, 1e-15))
                for name, value in clean.items()
            )
        )
    return {
        "score": round(min(TRUST_CEILING, score), 6),
        "axes": clean,
        "weights": {
            name: round(normalized_weights[name], 8) for name in normalized_weights
        },
        "status": "CONJECTURE_1_ADVISORY",
        "can_authorize": False,
        "can_be_sole_allow_basis": False,
        "human_review_required": True,
        "truth_label": "MODELED",
    }


def cost_per_success(cost_usd: float, successful_outcomes: int) -> float | None:
    cost = finite_float(cost_usd, name="cost_usd", minimum=0.0)
    successful = int(successful_outcomes)
    if successful <= 0:
        return None
    return cost / successful


