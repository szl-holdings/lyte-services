"""Lyte business-observability formulas, sources, memory, and receipts.

Lyte links technical reliability, agent operations, cost, and explicit business
outcomes. It does not infer causality from correlation and never executes a
remediation. All external destinations are fixed to public GitHub API routes.
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
from collections import OrderedDict
from statistics import fmean
from typing import Any, Iterable, Mapping
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx

VERSION = "2.0.0"
SOURCE_REPOSITORY = "szl-holdings/lyte-services"
TRUST_CEILING = 0.97
SESSION_TOKEN = re.compile(r"^[A-Za-z0-9._~-]{32,128}$")
REPOSITORY = re.compile(r"^[a-z0-9][a-z0-9._-]{0,99}$")
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
USER_AGENT = "SZL-Lyte-Business-Observability/2.0 (+https://a-11-oy.com)"


class SourceUnavailable(RuntimeError):
    """A fixed public source did not produce a valid observation."""


def canonical_json(value: Any) -> str:
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


def percentile(values: Iterable[float], quantile: float) -> float | None:
    """Linear-interpolated percentile with an explicit [0, 1] quantile."""
    clean = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not clean:
        return None
    q = clamp01(quantile)
    if len(clean) == 1:
        return clean[0]
    position = (len(clean) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return clean[lower]
    fraction = position - lower
    return clean[lower] + (clean[upper] - clean[lower]) * fraction


def availability_sli(good_events: int, total_events: int) -> float | None:
    if total_events <= 0:
        return None
    good = max(0, min(int(good_events), int(total_events)))
    return good / int(total_events)


def error_budget(
    *,
    good_events: int,
    total_events: int,
    target: float,
) -> dict[str, Any]:
    """Return SLI, burn rate, and remaining error budget for a count SLO."""
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
    remaining = 1.0 - burn
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
        "error_budget_remaining": round(max(0.0, remaining), 6),
        "state": state,
        "truth_label": "MEASURED",
    }


def outcome_attainment(
    *,
    current: float,
    target: float,
    direction: str,
) -> float:
    current_value = float(current)
    target_value = float(target)
    if not math.isfinite(current_value) or not math.isfinite(target_value):
        raise ValueError("outcome values must be finite")
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


def weighted_geometric_mean(axes: Mapping[str, float]) -> dict[str, Any]:
    """Canonical advisory Lambda shape; uniqueness remains Conjecture 1."""
    if not axes:
        raise ValueError("at least one axis is required")
    clean: dict[str, float] = {}
    for name, value in axes.items():
        clean[name] = clamp01(float(value))
    if any(value == 0.0 for value in clean.values()):
        score = 0.0
    else:
        weight = 1.0 / len(clean)
        score = math.exp(sum(weight * math.log(value) for value in clean.values()))
    return {
        "score": round(min(TRUST_CEILING, score), 6),
        "axes": clean,
        "weights": {name: round(1.0 / len(clean), 6) for name in clean},
        "status": "CONJECTURE_1_ADVISORY",
        "can_authorize": False,
        "truth_label": "MODELED",
    }


def cost_per_success(cost_usd: float, successful_outcomes: int) -> float | None:
    cost = float(cost_usd)
    if not math.isfinite(cost) or cost < 0:
        raise ValueError("cost_usd must be finite and non-negative")
    if successful_outcomes <= 0:
        return None
    return cost / int(successful_outcomes)


def agent_trace_summary(traces: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate caller-supplied agent traces without retaining prompt content."""
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
            "truth_label": "UNAVAILABLE",
        }
    latency: list[float] = []
    total_cost = 0.0
    total_input = 0
    total_output = 0
    successes = 0
    privacy_flags = 0
    safety_flags = 0
    for trace in traces:
        value = float(trace.get("latency_ms", 0.0))
        if not math.isfinite(value) or value < 0:
            raise ValueError("trace latency_ms must be finite and non-negative")
        latency.append(value)
        cost = float(trace.get("cost_usd", 0.0))
        if not math.isfinite(cost) or cost < 0:
            raise ValueError("trace cost_usd must be finite and non-negative")
        total_cost += cost
        total_input += max(0, int(trace.get("input_tokens", 0)))
        total_output += max(0, int(trace.get("output_tokens", 0)))
        successes += bool(trace.get("success", False))
        privacy_flags += bool(trace.get("privacy_flag", False))
        safety_flags += bool(trace.get("safety_flag", False))
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
        "truth_label": "MEASURED",
    }


def _service_priority(
    *,
    burn_rate: float | None,
    revenue_at_risk_usd: float,
    customer_impact: float,
) -> float:
    burn_component = min(4.0, max(0.0, burn_rate or 0.0)) / 4.0
    impact = clamp01(customer_impact)
    revenue = clamp01(math.log10(1.0 + max(0.0, revenue_at_risk_usd)) / 8.0)
    return round((0.45 * burn_component) + (0.35 * impact) + (0.20 * revenue), 6)


def analyze_window(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Build a deterministic service-to-outcome review envelope."""
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
    for item in outcomes:
        if not isinstance(item, Mapping):
            raise ValueError("each outcome must be an object")
        name = str(item.get("name", "")).strip()
        if not name:
            raise ValueError("outcome name is required")
        score = outcome_attainment(
            current=float(item.get("current", 0.0)),
            target=float(item.get("target", 0.0)),
            direction=str(item.get("direction", "")),
        )
        weight = clamp01(float(item.get("weight", 1.0)))
        outcome_scores[name] = score
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
    queue: list[dict[str, Any]] = []
    graph_edges: list[dict[str, Any]] = []
    for item in services:
        if not isinstance(item, Mapping):
            raise ValueError("each service must be an object")
        name = str(item.get("name", "")).strip()
        if not name:
            raise ValueError("service name is required")
        good = int(item.get("good_events", 0))
        total = int(item.get("total_events", 0))
        if good > total:
            raise ValueError("good_events cannot exceed total_events")
        target = float(item.get("slo_target", 0.99))
        budget = error_budget(
            good_events=good,
            total_events=total,
            target=target,
        )
        latency_values = [
            float(value)
            for value in item.get("latency_ms", [])
            if math.isfinite(float(value)) and float(value) >= 0
        ]
        cost = float(item.get("cost_usd", 0.0))
        successful = int(item.get("successful_outcomes", good))
        revenue_at_risk = max(0.0, float(item.get("revenue_at_risk_usd", 0.0)))
        customer_impact = clamp01(float(item.get("customer_impact", 0.0)))
        linked_outcomes = [
            str(value)
            for value in item.get("outcome_names", [])
            if str(value) in outcome_scores
        ]
        linked_score = (
            fmean(outcome_scores[name] for name in linked_outcomes)
            if linked_outcomes
            else 0.5
        )
        sli = budget["sli"]
        reliability = sli if isinstance(sli, float) else 0.0
        unit_cost = cost_per_success(cost, successful)
        cost_score = clamp01(
            1.0
            - (
                (unit_cost if unit_cost is not None else cost)
                / max(float(item.get("cost_budget_per_success_usd", 100.0)), 1e-9)
            )
        )
        service_lambda = weighted_geometric_mean(
            {
                "reliability": reliability,
                "outcome_attainment": linked_score,
                "cost_efficiency": cost_score,
                "customer_safety": 1.0 - customer_impact,
            }
        )
        priority = _service_priority(
            burn_rate=budget["burn_rate"],
            revenue_at_risk_usd=revenue_at_risk,
            customer_impact=customer_impact,
        )
        p50 = percentile(latency_values, 0.50)
        p95 = percentile(latency_values, 0.95)
        row = {
            "name": name,
            "slo": budget,
            "latency": {
                "p50_ms": round(p50, 3) if p50 is not None else None,
                "p95_ms": round(p95, 3) if p95 is not None else None,
                "sample_count": len(latency_values),
                "truth_label": "MEASURED" if latency_values else "UNAVAILABLE",
            },
            "cost": {
                "total_usd": round(cost, 6),
                "successful_outcomes": successful,
                "per_success_usd": round(unit_cost, 6) if unit_cost is not None else None,
                "budget_per_success_usd": float(
                    item.get("cost_budget_per_success_usd", 100.0)
                ),
                "truth_label": "MEASURED",
            },
            "business": {
                "outcome_names": linked_outcomes,
                "outcome_attainment": round(linked_score, 6),
                "revenue_at_risk_usd": round(revenue_at_risk, 2),
                "customer_impact": round(customer_impact, 6),
                "causality_claimed": False,
            },
            "lambda_advisory": service_lambda,
            "priority": priority,
            "truth_label": "MEASURED",
        }
        service_rows.append(row)
        if budget["state"] != "HEALTHY" or priority >= 0.35:
            queue.append(
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
                }
            )
        for outcome_name in linked_outcomes:
            graph_edges.append(
                {
                    "from": name,
                    "to": outcome_name,
                    "relation": "DECLARED_DEPENDENCY",
                    "causality_claimed": False,
                }
            )

    queue.sort(key=lambda item: (-item["priority"], item["service"]))
    service_health = fmean(
        row["lambda_advisory"]["score"] for row in service_rows
    )
    outcome_health = fmean(outcome_scores.values()) if outcome_scores else 0.5
    agent_health = (
        trace_summary["success_rate"]
        if isinstance(trace_summary["success_rate"], float)
        else 0.5
    )
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
        }
    )
    evidence_refs = [
        str(value).strip()
        for value in payload.get("evidence_refs", [])
        if str(value).strip()
    ]
    evidence_digests = [
        hashlib.sha256(value.encode("utf-8")).hexdigest()
        for value in evidence_refs
    ]
    return {
        "schema": "szl.lyte-window/v2",
        "version": VERSION,
        "services": service_rows,
        "outcomes": outcome_rows,
        "agent_traces": trace_summary,
        "service_outcome_graph": {
            "edges": graph_edges,
            "edge_count": len(graph_edges),
            "causality_claimed": False,
        },
        "action_queue": queue,
        "portfolio_lambda": portfolio_lambda,
        "evidence_ref_sha256": evidence_digests,
        "raw_evidence_refs_recorded": False,
        "effectors_enabled": False,
        "human_approval_required": True,
        "truth_label": "MODELED",
    }


def analysis_receipt(
    *,
    scope: str,
    analysis: Mapping[str, Any],
    observed_at: float | None = None,
) -> dict[str, Any]:
    timestamp = float(observed_at if observed_at is not None else time.time())
    source = {
        "schema": "szl.lyte-analysis-receipt/v2",
        "analysis_sha256": sha256_json(analysis),
        "session_scope_sha256": scope,
        "observed_at": timestamp,
        "source_repository": SOURCE_REPOSITORY,
        "signature_claimed": False,
        "effectors_enabled": False,
    }
    return {
        **source,
        "receipt_id": sha256_json(source),
        "truth_label": "MEASURED",
    }


def session_scope(token: str) -> str:
    value = token.strip()
    if SESSION_TOKEN.fullmatch(value) is None:
        raise ValueError(
            "X-SZL-Session must be 32-128 characters using A-Z, a-z, 0-9, . _ ~ or -"
        )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class SessionLedger:
    """Bounded memory keyed by a non-reversible caller session digest."""

    def __init__(self, max_sessions: int = 128, per_session: int = 100) -> None:
        self.max_sessions = max_sessions
        self.per_session = per_session
        self._rows: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
        self._lock = threading.RLock()

    def append(
        self,
        scope: str,
        *,
        kind: str,
        receipt: Mapping[str, Any],
        summary: Mapping[str, Any],
    ) -> None:
        safe = {
            "kind": kind,
            "receipt_id": receipt.get("receipt_id"),
            "observed_at": receipt.get("observed_at"),
            "truth_label": receipt.get("truth_label"),
            "summary": dict(summary),
        }
        with self._lock:
            rows = self._rows.pop(scope, [])
            rows.append(safe)
            self._rows[scope] = rows[-self.per_session :]
            while len(self._rows) > self.max_sessions:
                self._rows.popitem(last=False)

    def recent(
        self,
        scope: str,
        *,
        limit: int = 25,
        kind: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = list(self._rows.get(scope, []))
        if kind:
            rows = [row for row in rows if row["kind"] == kind]
        return list(reversed(rows[-max(1, min(limit, self.per_session)) :]))

    def receipt_ids(self, scope: str) -> set[str]:
        return {
            str(row["receipt_id"])
            for row in self.recent(scope, limit=self.per_session)
            if isinstance(row.get("receipt_id"), str)
        }

    def status(self) -> dict[str, Any]:
        with self._lock:
            session_count = len(self._rows)
            observation_count = sum(len(rows) for rows in self._rows.values())
        return {
            "durability": "EPHEMERAL_PROCESS_MEMORY",
            "scope": "SHA256_CALLER_SESSION",
            "sessions": session_count,
            "observations": observation_count,
            "max_sessions": self.max_sessions,
            "max_observations_per_session": self.per_session,
            "raw_session_tokens_recorded": False,
        }


LEDGER = SessionLedger()


def _bounded_get_json(
    url: str,
    *,
    query: Mapping[str, str],
    max_bytes: int,
    transport: httpx.BaseTransport | None = None,
) -> tuple[bytes, Any, str]:
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or parts.hostname != "api.github.com"
        or parts.username
        or parts.password
    ):
        raise SourceUnavailable("source destination failed the fixed allowlist")
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_READ_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with httpx.Client(
            timeout=httpx.Timeout(15.0, connect=5.0),
            follow_redirects=False,
            transport=transport,
        ) as client:
            with client.stream("GET", url, params=query, headers=headers) as response:
                if 300 <= response.status_code < 400:
                    raise SourceUnavailable("upstream redirect rejected")
                if response.status_code < 200 or response.status_code >= 300:
                    raise SourceUnavailable(
                        f"GitHub returned HTTP {response.status_code}"
                    )
                content_length = response.headers.get("content-length")
                if content_length and int(content_length) > max_bytes:
                    raise SourceUnavailable("upstream response exceeds byte budget")
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise SourceUnavailable("upstream response exceeds byte budget")
                    chunks.append(chunk)
                raw = b"".join(chunks)
                try:
                    payload = json.loads(raw)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise SourceUnavailable("GitHub returned invalid JSON") from exc
    except SourceUnavailable:
        raise
    except (httpx.HTTPError, OSError, ValueError) as exc:
        raise SourceUnavailable(
            f"source transport failed: {type(exc).__name__}"
        ) from exc
    source_url = urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            parts.path,
            urlencode(dict(query)),
            "",
        )
    )
    return raw, payload, source_url


def _parse_iso(value: Any) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def github_workflow_observation(
    repository: str,
    *,
    limit: int = 20,
    transport: httpx.BaseTransport | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    repo = repository.strip().lower()
    if REPOSITORY.fullmatch(repo) is None or repo not in GITHUB_REPOSITORIES:
        raise ValueError("repository is not in the Lyte observability allowlist")
    bounded_limit = max(1, min(50, int(limit)))
    url = f"https://api.github.com/repos/szl-holdings/{repo}/actions/runs"
    query = {"per_page": str(bounded_limit)}
    raw, payload, source_url = _bounded_get_json(
        url,
        query=query,
        max_bytes=4_000_000,
        transport=transport,
    )
    if not isinstance(payload, dict) or not isinstance(
        payload.get("workflow_runs"), list
    ):
        raise SourceUnavailable("GitHub workflow payload schema is not recognized")
    rows: list[dict[str, Any]] = []
    completed = 0
    successful = 0
    failed = 0
    running = 0
    durations: list[float] = []
    for item in payload["workflow_runs"][:bounded_limit]:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "UNAVAILABLE")
        conclusion = item.get("conclusion")
        if status == "completed":
            completed += 1
            successful += conclusion == "success"
            failed += conclusion not in {None, "success", "skipped", "neutral"}
        else:
            running += 1
        start = _parse_iso(item.get("run_started_at") or item.get("created_at"))
        end = _parse_iso(item.get("updated_at"))
        duration = (
            max(0.0, end - start)
            if start is not None and end is not None and end >= start
            else None
        )
        if duration is not None:
            durations.append(duration)
        rows.append(
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "display_title": item.get("display_title"),
                "head_branch": item.get("head_branch"),
                "head_sha": item.get("head_sha"),
                "event": item.get("event"),
                "status": status,
                "conclusion": conclusion,
                "run_number": item.get("run_number"),
                "created_at": item.get("created_at"),
                "updated_at": item.get("updated_at"),
                "duration_seconds": (
                    round(duration, 3) if duration is not None else None
                ),
                "html_url": item.get("html_url"),
            }
        )
    success_rate = successful / completed if completed else None
    observation = {
        "repository": f"szl-holdings/{repo}",
        "runs": rows,
        "returned": len(rows),
        "completed": completed,
        "successful": successful,
        "failed": failed,
        "running": running,
        "success_rate": round(success_rate, 8) if success_rate is not None else None,
        "p50_duration_seconds": (
            round(percentile(durations, 0.50) or 0.0, 3) if durations else None
        ),
        "p95_duration_seconds": (
            round(percentile(durations, 0.95) or 0.0, 3) if durations else None
        ),
        "source": "GitHub Actions public REST API",
        "truth_label": "REPORTED",
    }
    observed_at = time.time()
    body = {
        "schema": "szl.lyte-source-receipt/v2",
        "source_id": "github-actions",
        "source_url": source_url,
        "repository": f"szl-holdings/{repo}",
        "payload_sha256": hashlib.sha256(raw).hexdigest(),
        "observation_sha256": sha256_json(observation),
        "observed_at": observed_at,
        "expires_at": observed_at + 120,
        "state": "OBSERVED",
        "signature_claimed": False,
    }
    receipt = {
        **body,
        "receipt_id": sha256_json(body),
        "truth_label": "REPORTED",
    }
    return observation, receipt
