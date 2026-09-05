"""Lyte Enterprise Signal Lattice — operational FastAPI runtime."""
from __future__ import annotations

import hashlib
import html
import json
import math
import os
import re
import threading
import time
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from a11oy_factory.cells import FRONTIERS, LYTE
from a11oy_factory.compiler import BLOCKED, compile_cell
from a11oy_factory.jobs import JOBS, search_jobs
from lyte_api.catalog import ANATOMY, FORMULAS, POSITIONING
from lyte_engine import (
    GITHUB_REPOSITORIES,
    LEDGER,
    LENSES,
    SOURCE_REPOSITORY,
    TRUST_CEILING,
    VERSION,
    SourceUnavailable,
    analyze_journey,
    analyze_window,
    answer_question,
    github_workflow_observation,
    normalize_otel_spans,
    observation_receipt,
    rebind_receipt_scope,
    session_scope,
    sha256_json,
    summarize_atlas_events,
    weighted_geometric_mean,
)

ROOT = Path(__file__).resolve().parent
REPOSITORY_ROOT = ROOT.parent
HTML = ROOT / "index.html"
SOURCE_FILE = REPOSITORY_ROOT / "source_revision.txt"
CONTAINER_SOURCE_FILE = Path("/app/source_revision.txt")
SHA40 = re.compile(r"^[0-9a-f]{40}$")
RECEIPT64 = re.compile(r"^[0-9a-f]{64}$")
MAX_REQUEST_BYTES = 512_000
RUNTIME_IDENTITY_FIELDS = (
    "source_repository",
    "source_revision",
    "runtime_repository",
    "runtime_source_revision",
    "effectors_enabled",
    "human_approval_required",
)

app = FastAPI(
    title="Lyte Enterprise Signal Lattice",
    version=VERSION,
    description=(
        "Governed business observability across services, customer journeys, "
        "AI agents, delivery, cost, risk, decisions, evidence, and outcomes."
    ),
)

_METRICS: Counter[str] = Counter()
_METRICS_LOCK = threading.RLock()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AnalyzeRequest(StrictModel):
    services: list[dict[str, Any]] = Field(min_length=1, max_length=100)
    outcomes: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    traces: list[dict[str, Any]] = Field(default_factory=list, max_length=500)
    evidence_refs: list[str] = Field(default_factory=list, max_length=64)


class JourneyRequest(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    stages: list[dict[str, Any]] = Field(min_length=1, max_length=50)


class SpanBatch(StrictModel):
    spans: list[dict[str, Any]] = Field(min_length=1, max_length=5000)


class AtlasBatch(StrictModel):
    events: list[dict[str, Any]] = Field(min_length=1, max_length=2000)


class AskRequest(StrictModel):
    question: str = Field(min_length=1, max_length=500)


class CompileRequest(StrictModel):
    cell: str = Field(min_length=1, max_length=32)
    signal: str = Field(default="", max_length=1000)
    prev_hash: str = Field(default="0" * 64, min_length=64, max_length=64)


class ActRequest(StrictModel):
    cell: str = Field(min_length=1, max_length=32)
    payload: dict[str, Any] = Field(default_factory=dict)


class HatunRequest(StrictModel):
    intent: str = Field(min_length=1, max_length=240)
    requested_action: str = Field(default="observe.review", min_length=2, max_length=64)
    axes: dict[str, float]
    evidence_receipt_ids: list[str] = Field(default_factory=list, max_length=64)

    @field_validator("intent")
    @classmethod
    def clean_intent(cls, value: str) -> str:
        result = " ".join(value.split())
        if not result:
            raise ValueError("intent must not be blank")
        return result

    @field_validator("requested_action")
    @classmethod
    def clean_action(cls, value: str) -> str:
        result = value.strip()
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]{1,63}", result) is None:
            raise ValueError("requested_action is invalid")
        return result

    @field_validator("axes")
    @classmethod
    def validate_axes(cls, value: dict[str, float]) -> dict[str, float]:
        if not 2 <= len(value) <= 16:
            raise ValueError("axes must contain between 2 and 16 values")
        clean: dict[str, float] = {}
        for raw_name, raw_value in value.items():
            name = raw_name.strip().lower()
            numeric = float(raw_value)
            if re.fullmatch(r"[a-z][a-z0-9_.-]{1,63}", name) is None:
                raise ValueError(f"invalid axis identifier: {raw_name!r}")
            if not math.isfinite(numeric) or not 0.0 <= numeric <= 1.0:
                raise ValueError(f"axis {name!r} must be finite and in [0,1]")
            clean[name] = numeric
        return clean

    @field_validator("evidence_receipt_ids")
    @classmethod
    def validate_receipts(cls, values: list[str]) -> list[str]:
        clean = [value.strip().lower() for value in values]
        if any(RECEIPT64.fullmatch(value) is None for value in clean):
            raise ValueError("evidence receipt IDs must be 64 lowercase hex characters")
        if len(clean) != len(set(clean)):
            raise ValueError("evidence receipt IDs must be unique")
        return clean


def _source_observation() -> dict[str, Any]:
    candidates: list[tuple[str, str]] = []
    for name in ("LYTE_SOURCE_REVISION", "SZL_SOURCE_REVISION"):
        value = os.environ.get(name, "").strip().lower()
        if SHA40.fullmatch(value):
            candidates.append((f"env:{name}", value))
    for label, path in (
        ("repository-file", SOURCE_FILE),
        ("container-file", CONTAINER_SOURCE_FILE),
    ):
        try:
            value = path.read_text(encoding="ascii").strip().lower()
        except OSError:
            continue
        if SHA40.fullmatch(value):
            candidates.append((label, value))
    revisions = sorted({revision for _, revision in candidates})
    if not revisions:
        state, revision = "UNBOUND", "UNAVAILABLE"
    elif len(revisions) == 1:
        state, revision = "OBSERVED", revisions[0]
    else:
        state, revision = "MISMATCH", revisions[0]
    return {
        "state": state,
        "revision": revision,
        "bindings_agree": len(revisions) <= 1,
        "evidence_sources": sorted({label for label, _ in candidates}),
    }


def _runtime_identity(source: dict[str, Any] | None = None) -> dict[str, Any]:
    observation = source if source is not None else _source_observation()
    revision = observation["revision"]
    return {
        "source_repository": SOURCE_REPOSITORY,
        "source_revision": revision,
        "runtime_repository": SOURCE_REPOSITORY,
        "runtime_source_revision": revision,
        "effectors_enabled": False,
        "human_approval_required": True,
    }


def build_info() -> dict[str, Any]:
    source = _source_observation()
    return {
        "schema": "szl.build-info/v1",
        "service": "lyte-signal-lattice",
        "version": VERSION,
        **_runtime_identity(source),
        "build": {
            "state": source["state"],
            "revision": source["revision"],
        },
        "source_binding": {
            "bindings_agree": source["bindings_agree"],
            "evidence_sources": source["evidence_sources"],
        },
        "receipt_minted": False,
        "truth_label": "MEASURED",
    }


def _require_scope(value: str) -> str:
    try:
        return session_scope(value)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def _record(
    *,
    scope: str,
    kind: str,
    payload: dict[str, Any],
    truth_label: str,
    source_url: str,
    summary: dict[str, Any],
) -> dict[str, Any]:
    try:
        receipt = observation_receipt(
            scope=scope,
            kind=kind,
            payload=payload,
            truth_label=truth_label,
            source_url=source_url,
        )
        LEDGER.append(
            scope,
            kind=kind,
            receipt=receipt,
            summary=summary,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return receipt


def _scenario_inputs() -> dict[str, Any]:
    return {
        "analysis": {
            "services": [
                {
                    "name": "checkout-api",
                    "good_events": 99410,
                    "total_events": 100000,
                    "slo_target": 0.999,
                    "latency_ms": [92, 98, 104, 121, 149, 188, 244, 381, 640],
                    "cost_usd": 482.25,
                    "successful_outcomes": 99410,
                    "cost_budget_per_success_usd": 0.01,
                    "revenue_at_risk_usd": 184200,
                    "customer_impact": 0.72,
                    "severity": 0.84,
                    "dependencies": ["payments-api", "inventory-api"],
                    "outcome_names": ["Checkout completion"],
                    "evidence_quality": 0.92,
                    "health_trend": [0.96, 0.94, 0.83, 0.68],
                },
                {
                    "name": "payments-api",
                    "good_events": 98880,
                    "total_events": 100000,
                    "slo_target": 0.999,
                    "latency_ms": [88, 101, 130, 176, 288, 510, 920],
                    "cost_usd": 319.40,
                    "successful_outcomes": 98880,
                    "cost_budget_per_success_usd": 0.01,
                    "revenue_at_risk_usd": 142700,
                    "customer_impact": 0.81,
                    "severity": 0.91,
                    "dependencies": ["payment-gateway"],
                    "outcome_names": ["Checkout completion"],
                    "evidence_quality": 0.89,
                    "health_trend": [0.97, 0.92, 0.74, 0.55],
                },
                {
                    "name": "support-agent",
                    "good_events": 920,
                    "total_events": 1000,
                    "slo_target": 0.95,
                    "latency_ms": [860, 940, 1210, 1540, 2380],
                    "cost_usd": 712.20,
                    "successful_outcomes": 920,
                    "cost_budget_per_success_usd": 0.55,
                    "revenue_at_risk_usd": 21500,
                    "customer_impact": 0.41,
                    "severity": 0.46,
                    "dependencies": ["retrieval-service"],
                    "outcome_names": ["Support resolution"],
                    "evidence_quality": 0.86,
                    "health_trend": [0.89, 0.86, 0.82, 0.75],
                },
            ],
            "outcomes": [
                {
                    "name": "Checkout completion",
                    "current": 0.941,
                    "target": 0.985,
                    "direction": "higher",
                    "weight": 0.7,
                },
                {
                    "name": "Support resolution",
                    "current": 0.82,
                    "target": 0.92,
                    "direction": "higher",
                    "weight": 0.3,
                },
            ],
            "traces": [
                {
                    "trace_id": "agent-001",
                    "latency_ms": 1540,
                    "input_tokens": 2140,
                    "output_tokens": 412,
                    "cost_usd": 0.21,
                    "success": False,
                    "privacy_flag": False,
                    "safety_flag": False,
                    "tool_calls": 4,
                },
                {
                    "trace_id": "agent-002",
                    "latency_ms": 910,
                    "input_tokens": 1640,
                    "output_tokens": 355,
                    "cost_usd": 0.16,
                    "success": True,
                    "privacy_flag": False,
                    "safety_flag": False,
                    "tool_calls": 3,
                },
            ],
            "evidence_refs": [
                "sample://checkout/deployment-742",
                "sample://journey/checkout-window",
            ],
        },
        "journey": {
            "name": "Checkout purchase",
            "stages": [
                {
                    "name": "Cart",
                    "service": "checkout-api",
                    "volume": 100000,
                    "success_rate": 0.982,
                    "target_success_rate": 0.99,
                    "value_per_success_usd": 84.0,
                    "weight": 0.20,
                },
                {
                    "name": "Payment",
                    "service": "payments-api",
                    "volume": 98200,
                    "success_rate": 0.941,
                    "target_success_rate": 0.985,
                    "value_per_success_usd": 84.0,
                    "weight": 0.55,
                },
                {
                    "name": "Confirmation",
                    "service": "checkout-api",
                    "volume": 92406,
                    "success_rate": 0.994,
                    "target_success_rate": 0.998,
                    "value_per_success_usd": 84.0,
                    "weight": 0.25,
                },
            ],
        },
        "otel": {
            "spans": [
                {
                    "trace_id": "trace-742",
                    "span_id": "root",
                    "service": "checkout-api",
                    "name": "POST /checkout",
                    "duration_ms": 640,
                    "status": "ERROR",
                    "attributes": {"deployment.id": "742"},
                },
                {
                    "trace_id": "trace-742",
                    "span_id": "pay",
                    "parent_span_id": "root",
                    "service": "payments-api",
                    "name": "authorize",
                    "duration_ms": 510,
                    "status": "ERROR",
                    "attributes": {"provider": "sample-gateway"},
                },
            ]
        },
        "atlas": {
            "events": [
                {
                    "event_id": "deploy-742",
                    "event_class": "business.risk.detected",
                    "domain": "commerce",
                    "severity": "high",
                    "timestamp": 1788541200000,
                    "business_value": {
                        "amount": 184200,
                        "currency": "USD",
                        "type": "at-risk",
                        "description": "Modeled checkout value at risk",
                    },
                    "slo_impact": {
                        "impact": "breached",
                        "slo_id": "checkout-availability",
                    },
                    "correlation_id": "trace-742",
                    "workflow_id": "deployment-742",
                }
            ]
        },
    }


def scenario() -> dict[str, Any]:
    inputs = _scenario_inputs()
    analysis = analyze_window(inputs["analysis"])
    journey = analyze_journey(inputs["journey"])
    otel = normalize_otel_spans(inputs["otel"]["spans"])
    atlas = summarize_atlas_events(inputs["atlas"]["events"])
    playback = [
        {
            "offset_minutes": -30,
            "state": "HEALTHY",
            "event": "Pre-deployment baseline",
            "posture": 0.91,
            "truth_label": "SAMPLE",
        },
        {
            "offset_minutes": -5,
            "state": "WATCH",
            "event": "Deployment 742 completed",
            "posture": 0.79,
            "truth_label": "SAMPLE",
        },
        {
            "offset_minutes": 0,
            "state": "CRITICAL",
            "event": "Payment latency and error-budget burn increase",
            "posture": analysis["enterprise_state"]["posture_score"],
            "truth_label": "SAMPLE",
        },
        {
            "offset_minutes": 15,
            "state": "REVIEW",
            "event": "Rollback action request prepared; not executed",
            "posture": 0.62,
            "truth_label": "SAMPLE",
        },
    ]
    return {
        "schema": "szl.lyte-enterprise-scenario/v3",
        "name": "Revenue-critical checkout degradation",
        "demo_mode": True,
        "truth_label": "SAMPLE",
        "inputs": inputs,
        "analysis": analysis,
        "journey": journey,
        "otel": otel,
        "atlas": atlas,
        "playback": playback,
        "action_execution": "DISABLED",
        "effectors_enabled": False,
        "human_approval_required": True,
    }


@app.middleware("http")
async def harden_and_measure(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_REQUEST_BYTES:
                with _METRICS_LOCK:
                    _METRICS["requests_rejected_body_size_total"] += 1
                return JSONResponse(
                    {"detail": "request body exceeds 512000 bytes"},
                    status_code=413,
                )
        except ValueError:
            return JSONResponse({"detail": "invalid content-length"}, status_code=400)

    started = time.perf_counter()
    with _METRICS_LOCK:
        _METRICS["http_requests_total"] += 1
    response = await call_next(request)
    duration = time.perf_counter() - started
    with _METRICS_LOCK:
        _METRICS[f"http_status_{response.status_code}_total"] += 1
        _METRICS["http_request_duration_microseconds_total"] += int(
            duration * 1_000_000
        )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = (
        "camera=(), microphone=(), geolocation=(), payment=()"
    )
    response.headers["Cross-Origin-Resource-Policy"] = "same-site"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'; connect-src 'self'; "
        "img-src 'self' data:; object-src 'none'; base-uri 'none'; "
        "form-action 'self'; frame-ancestors 'self' https://huggingface.co"
    )
    if request.url.path != "/":
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/", response_class=FileResponse)
def root() -> FileResponse:
    return FileResponse(HTML, media_type="text/html")


@app.get("/healthz")
def health() -> dict[str, Any]:
    compiler = compile_cell("lyte")
    source = _source_observation()
    return {
        "ok": (
            compiler.decision == "ALLOW"
            and HTML.is_file()
            and source["state"] == "OBSERVED"
            and source["bindings_agree"] is True
        ),
        "service": "lyte-signal-lattice",
        "version": VERSION,
        **_runtime_identity(source),
        "engine_imported": True,
        "front_door_present": HTML.is_file(),
        "compiler": {
            "cell": compiler.cell,
            "decision": compiler.decision,
            "honesty_tier": compiler.honesty_tier,
        },
        "memory": LEDGER.status(),
        "lenses": [item["id"] for item in LENSES],
        "causality_claimed": False,
        "lambda_status": "Conjecture 1 (advisory only)",
        "truth_label": "MEASURED",
    }


@app.get("/readyz")
def readiness() -> JSONResponse:
    build = build_info()
    compiler = compile_cell("lyte")
    ready = (
        build["build"]["state"] == "OBSERVED"
        and build["source_binding"]["bindings_agree"] is True
        and compiler.decision == "ALLOW"
        and HTML.is_file()
    )
    return JSONResponse(
        {
            "ready": ready,
            "service": "lyte-signal-lattice",
            "version": VERSION,
            **{key: build[key] for key in RUNTIME_IDENTITY_FIELDS},
            "build": build["build"],
            "source_binding": build["source_binding"],
            "compiler": compiler.decision,
            "front_door_present": HTML.is_file(),
            "memory": LEDGER.status(),
            "truth_label": "MEASURED",
        },
        status_code=200 if ready else 503,
    )


@app.get("/api/build-info")
def build_info_route() -> dict[str, Any]:
    return build_info()


@app.get("/api/source")
@app.get("/.well-known/szl-source.json")
def source_document() -> dict[str, Any]:
    return build_info()


@app.get("/metrics", response_class=PlainTextResponse)
def metrics() -> PlainTextResponse:
    with _METRICS_LOCK:
        rows = [
            "# TYPE lyte_http_requests_total counter",
            f"lyte_http_requests_total {_METRICS['http_requests_total']}",
            "# TYPE lyte_http_request_duration_microseconds_total counter",
            (
                "lyte_http_request_duration_microseconds_total "
                f"{_METRICS['http_request_duration_microseconds_total']}"
            ),
            "# TYPE lyte_observations gauge",
            f"lyte_observations {LEDGER.status()['observations']}",
        ]
        for key, value in sorted(_METRICS.items()):
            if key.startswith("http_status_"):
                code = key.split("_")[2]
                rows.append(f'lyte_http_responses_total{{status="{code}"}} {value}')
    return PlainTextResponse("\n".join(rows) + "\n")


@app.get("/api/lyte/v3/catalog")
def catalog() -> dict[str, Any]:
    return {
        "schema": "szl.lyte-catalog/v3",
        "product": "Lyte Enterprise Signal Lattice",
        "positioning": POSITIONING,
        "lenses": LENSES,
        "routes": {
            "scenario": "/api/lyte/v3/scenario",
            "analyze": "/api/lyte/v3/analyze",
            "journey": "/api/lyte/v3/journeys/analyze",
            "otel": "/api/lyte/v3/telemetry/otel",
            "atlas": "/api/lyte/v3/telemetry/atlas",
            "github": "/api/lyte/v3/github/{repository}",
            "ask": "/api/lyte/v3/ask",
            "second_brain": "/api/lyte/v3/second-brain",
            "playback": "/api/lyte/v3/playback",
            "hatun": "/api/lyte/v3/hatun/evaluate",
        },
        "effectors_enabled": False,
        "truth_label": "MEASURED",
    }


@app.get("/api/lyte/v3/capabilities")
def capabilities() -> dict[str, Any]:
    return {
        "schema": "szl.lyte-capabilities/v3",
        "service_observability": True,
        "slo_error_budget": True,
        "customer_journey_intelligence": True,
        "business_outcome_mapping": True,
        "ai_agent_operations": True,
        "delivery_observability": True,
        "economic_impact": True,
        "otel_shaped_ingest": True,
        "wire_compatible_otlp_collector": False,
        "atlas_business_events": True,
        "source_receipts": True,
        "second_brain": True,
        "ask_lyte": True,
        "incident_playback": True,
        "hatun_review": True,
        "automatic_remediation": False,
        "causal_inference": False,
        "effectors_enabled": False,
        "truth_label": "MEASURED",
    }


@app.get("/api/lyte/v3/anatomy")
def anatomy() -> dict[str, Any]:
    return {
        "schema": "szl.living-anatomy.lyte/v3",
        "organs": [
            {"order": index, "id": organ_id, "contract": contract}
            for index, (organ_id, contract) in enumerate(ANATOMY, start=1)
        ],
        "second_brain_scope": "SHA256_CALLER_SESSION",
        "hatun_decisions": ["REVIEW", "ABSTAIN", "DENY"],
        "truth_label": "MEASURED",
    }


@app.get("/api/lyte/v3/formulas")
def formulas() -> dict[str, Any]:
    return {
        "schema": "szl.formula-binding.lyte/v3",
        "formulas": FORMULAS,
        "count": len(FORMULAS),
        "trust_ceiling": TRUST_CEILING,
        "lambda_status": "Conjecture 1 (open) — advisory only",
        "can_authorize": False,
        "can_be_sole_allow_basis": False,
        "truth_label": "MEASURED",
    }


@app.get("/api/lyte/v3/sources")
def sources() -> dict[str, Any]:
    return {
        "schema": "szl.source-catalog.lyte/v3",
        "sources": [
            {
                "id": "github-actions",
                "authority": "GitHub Actions public REST API",
                "host": "api.github.com",
                "organization": "szl-holdings",
                "repository_allowlist": sorted(GITHUB_REPOSITORIES),
                "max_bytes": 4_000_000,
                "freshness_seconds": 120,
                "state": "WIRED",
            },
            {
                "id": "otel-shaped-json",
                "authority": "caller-supplied telemetry",
                "state": "WIRED",
                "wire_compatible_otlp_collector": False,
                "max_records": 5000,
            },
            {
                "id": "atlas-business-event",
                "authority": "caller-supplied governed business event",
                "state": "WIRED",
                "max_records": 2000,
            },
        ],
        "caller_supplied_fetch_urls_allowed": False,
        "redirects_allowed": False,
        "truth_label": "MEASURED",
    }


@app.get("/api/lyte/v3/scenario")
def sample_scenario() -> dict[str, Any]:
    return scenario()


@app.post("/api/lyte/v3/analyze")
def analyze(
    payload: AnalyzeRequest,
    x_szl_session: str = Header(..., alias="X-SZL-Session"),
) -> dict[str, Any]:
    scope = _require_scope(x_szl_session)
    try:
        result = analyze_window(payload.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    services = [
        {
            "name": item["name"],
            "burn_rate": item.get("slo", {}).get("burn_rate"),
            "priority": item.get("priority"),
        }
        for item in result["services"]
    ]
    receipt = _record(
        scope=scope,
        kind="analysis",
        payload=result,
        truth_label="MODELED",
        source_url="urn:szl:lyte:caller-supplied-analysis",
        summary={
            "services": services,
            "service_count": len(result["services"]),
            "outcome_count": len(result["outcomes"]),
            "agent_trace_count": result["agent_traces"]["trace_count"],
            "agent_success_rate": result["agent_traces"]["success_rate"],
            "revenue_at_risk_usd": result["enterprise_state"][
                "revenue_at_risk_usd"
            ],
            "posture_score": result["enterprise_state"]["posture_score"],
            "queue_items": result["enterprise_state"]["queue_items"],
            "causality_claimed": False,
        },
    )
    return {
        "analysis": result,
        "receipt": receipt,
        "session_token_recorded": False,
        "effectors_enabled": False,
        "truth_label": "MODELED",
    }


@app.post("/api/lyte/v3/journeys/analyze")
def journey_analyze(
    payload: JourneyRequest,
    x_szl_session: str = Header(..., alias="X-SZL-Session"),
) -> dict[str, Any]:
    scope = _require_scope(x_szl_session)
    try:
        result = analyze_journey(payload.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    receipt = _record(
        scope=scope,
        kind="journey",
        payload=result,
        truth_label="MODELED",
        source_url="urn:szl:lyte:caller-supplied-journey",
        summary={
            "name": result["name"],
            "status": result["status"],
            "stage_count": len(result["stages"]),
            "revenue_at_risk_usd": result["revenue_at_risk_usd"],
            "journey_health": result["journey_health"]["score"],
            "causality_claimed": False,
        },
    )
    return {
        "journey": result,
        "receipt": receipt,
        "session_token_recorded": False,
        "effectors_enabled": False,
        "truth_label": "MODELED",
    }


@app.post("/api/lyte/v3/telemetry/otel")
def otel_ingest(
    payload: SpanBatch,
    x_szl_session: str = Header(..., alias="X-SZL-Session"),
) -> dict[str, Any]:
    scope = _require_scope(x_szl_session)
    try:
        result = normalize_otel_spans(payload.spans)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    receipt = _record(
        scope=scope,
        kind="telemetry",
        payload=result,
        truth_label="MEASURED",
        source_url="urn:szl:lyte:otel-shaped-caller-ingest",
        summary={
            "span_count": result["span_count"],
            "trace_count": result["trace_count"],
            "service_count": result["service_count"],
            "orphan_span_count": result["orphan_span_count"],
            "sensitive_attributes_removed": True,
        },
    )
    return {
        "observation": result,
        "receipt": receipt,
        "session_token_recorded": False,
        "truth_label": "MEASURED",
    }


@app.post("/api/lyte/v3/telemetry/atlas")
def atlas_ingest(
    payload: AtlasBatch,
    x_szl_session: str = Header(..., alias="X-SZL-Session"),
) -> dict[str, Any]:
    scope = _require_scope(x_szl_session)
    try:
        result = summarize_atlas_events(payload.events)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    receipt = _record(
        scope=scope,
        kind="atlas",
        payload=result,
        truth_label="REPORTED",
        source_url="urn:szl:lyte:atlas-caller-ingest",
        summary={
            "event_count": result["event_count"],
            "priority": result["priority"],
            "business_value": result["business_value"],
            "causality_claimed": False,
        },
    )
    return {
        "observation": result,
        "receipt": receipt,
        "session_token_recorded": False,
        "truth_label": "REPORTED",
    }


@app.get("/api/lyte/v3/github/{repository}")
def github_source(
    repository: str,
    limit: int = Query(20, ge=1, le=50),
    x_szl_session: str = Header(..., alias="X-SZL-Session"),
) -> dict[str, Any]:
    scope = _require_scope(x_szl_session)
    try:
        observation, initial_receipt = github_workflow_observation(
            repository,
            limit=limit,
        )
        receipt = rebind_receipt_scope(initial_receipt, scope)
        LEDGER.append(
            scope,
            kind="source",
            receipt=receipt,
            summary={
                "repository": observation["repository"],
                "returned": observation["returned"],
                "success_rate": observation["success_rate"],
                "failed": observation["failed"],
                "running": observation["running"],
            },
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except SourceUnavailable as exc:
        raise HTTPException(502, str(exc)) from exc
    return {
        "observation": observation,
        "receipt": receipt,
        "session_token_recorded": False,
        "truth_label": "REPORTED",
    }


@app.get("/api/lyte/v3/second-brain")
def second_brain(
    limit: int = Query(25, ge=1, le=100),
    kind: str | None = Query(default=None, max_length=80),
    x_szl_session: str = Header(..., alias="X-SZL-Session"),
) -> dict[str, Any]:
    scope = _require_scope(x_szl_session)
    return {
        "schema": "szl.second-brain.lyte/v3",
        "scope": "SHA256_CALLER_SESSION",
        "memory": LEDGER.recent(scope, limit=limit, kind=kind),
        "memory_status": LEDGER.status(),
        "raw_session_token_recorded": False,
        "effectors_enabled": False,
        "truth_label": "MEASURED",
    }


@app.get("/api/lyte/v3/playback")
def playback(
    limit: int = Query(50, ge=1, le=100),
    x_szl_session: str = Header(..., alias="X-SZL-Session"),
) -> dict[str, Any]:
    scope = _require_scope(x_szl_session)
    records = LEDGER.recent(scope, limit=limit)
    return {
        "schema": "szl.lyte-playback/v3",
        "events": list(reversed(records)),
        "count": len(records),
        "causality_claimed": False,
        "effectors_enabled": False,
        "truth_label": "MEASURED",
    }


@app.get("/api/lyte/v3/receipts/{receipt_id}")
def receipt_lookup(
    receipt_id: str,
    x_szl_session: str = Header(..., alias="X-SZL-Session"),
) -> dict[str, Any]:
    scope = _require_scope(x_szl_session)
    result = LEDGER.get(scope, receipt_id.lower())
    if result is None:
        raise HTTPException(404, "receipt not found in caller session")
    return {
        "schema": "szl.lyte-receipt-view/v3",
        "receipt": result,
        "raw_session_token_recorded": False,
        "truth_label": "MEASURED",
    }


@app.post("/api/lyte/v3/ask")
def ask(
    payload: AskRequest,
    x_szl_session: str = Header(..., alias="X-SZL-Session"),
) -> dict[str, Any]:
    scope = _require_scope(x_szl_session)
    records = LEDGER.recent(scope, limit=100)
    try:
        return answer_question(payload.question, records)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/lyte/v3/hatun/evaluate")
def hatun_evaluate(
    review: HatunRequest,
    x_szl_session: str = Header(..., alias="X-SZL-Session"),
) -> dict[str, Any]:
    scope = _require_scope(x_szl_session)
    known = LEDGER.receipt_ids(scope)
    evidence_known = bool(review.evidence_receipt_ids) and set(
        review.evidence_receipt_ids
    ).issubset(known)
    rollup = weighted_geometric_mean(review.axes)
    blockers: list[str] = []
    deny = False
    if any(
        name in review.axes and review.axes[name] < 0.5
        for name in ("policy", "safety", "privacy")
    ):
        blockers.append("POLICY_OR_SAFETY_AXIS_BELOW_FLOOR")
        deny = True
    if any(
        term in review.requested_action.casefold()
        for term in ("execute", "delete", "disable", "terminate", "trade")
    ):
        blockers.append("REQUESTED_ACTION_REQUIRES_DISABLED_EFFECTOR")
        deny = True
    if not known:
        blockers.append("NO_SESSION_OBSERVATIONS")
    if not review.evidence_receipt_ids:
        blockers.append("NO_EVIDENCE_RECEIPTS")
    elif not evidence_known:
        blockers.append("UNKNOWN_EVIDENCE_RECEIPT")
    if rollup["score"] < 0.80:
        blockers.append("LAMBDA_BELOW_REVIEW_FLOOR")

    if deny:
        decision: Literal["REVIEW", "ABSTAIN", "DENY"] = "DENY"
    elif blockers:
        decision = "ABSTAIN"
    else:
        decision = "REVIEW"

    basis = {
        "schema": "szl.hatun-policy/v1",
        "authority_mode": "HUMAN_SOVEREIGN",
        "default_disposition": "DENY_EFFECTOR",
        "intent": review.intent,
        "requested_action": review.requested_action,
        "decision": decision,
        "blockers": blockers,
        "evidence_receipt_ids": review.evidence_receipt_ids,
        "evidence_known_in_session": evidence_known,
        "formula_ids": ["szl.lambda_advisory"],
        "lambda_score": rollup["score"],
        "lambda_status": rollup["status"],
        "can_authorize": False,
        "can_execute": False,
        "effectors_enabled": False,
        "human_approval_required": True,
        "session_token_recorded": False,
        "credential_material_recorded": False,
    }
    return {
        **basis,
        "receipt": {
            "schema": "szl.hatun-review-receipt/v1",
            "basis_sha256": sha256_json(basis),
            "signature_claimed": False,
        },
        "truth_label": "MODELED",
    }


@app.get("/api/cells")
def cells() -> list[dict[str, Any]]:
    return [asdict(LYTE), *[asdict(cell) for cell in FRONTIERS]]


@app.get("/api/jobs")
def jobs() -> list[dict[str, Any]]:
    return [asdict(job) for job in JOBS]


@app.get("/api/search")
def search(q: str = Query(default="", max_length=200)) -> dict[str, Any]:
    return search_jobs(q)


@app.get("/api/roadmap")
def roadmap() -> dict[str, Any]:
    return {
        "admitted": ["lyte"],
        "frontiers": [asdict(cell) for cell in FRONTIERS],
        "lambda_status": "Conjecture 1",
        "energy": None,
        "effectors_enabled": False,
        "truth_label": "MEASURED",
    }


@app.post("/api/compile")
def compile_route(payload: CompileRequest) -> dict[str, Any]:
    return compile_cell(
        payload.cell,
        signal=payload.signal,
        prev_hash=payload.prev_hash,
    ).as_dict()


@app.post("/api/act")
def act_route(payload: ActRequest) -> dict[str, Any]:
    compiler = compile_cell(payload.cell)
    return {
        "schema": "szl.lyte-action-gate/v3",
        "cell": compiler.cell,
        "compiler_decision": compiler.decision,
        "decision": BLOCKED,
        "reason": (
            "Lyte is admitted for observation and governed review only. "
            "Production effectors are disabled."
        ),
        "payload_sha256": hashlib.sha256(
            json.dumps(
                payload.payload,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
        "can_execute": False,
        "effectors_enabled": False,
        "human_approval_required": True,
        "truth_label": "MEASURED",
    }
