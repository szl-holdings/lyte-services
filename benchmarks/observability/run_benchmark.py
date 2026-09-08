# ruff: noqa: E501
"""Run Lyte's open, vendor-neutral business-observability benchmark.

The runner intentionally measures only Lyte. Its comparison basis is the
versioned acceptance-gate file committed beside this module, not an unobserved
competitor. Browser-only dimensions remain NOT_TESTED until browser evidence is
actually collected.
"""

from __future__ import annotations

import argparse
import copy
import csv
import ctypes
import gzip
import hashlib
import json
import math
import os
import platform
import shlex
import sqlite3
import sys
import time
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from fastapi.testclient import TestClient

from lyte.app import create_app
from lyte.domain import sha256_json

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "checkout-observability-v1.json"
TARGETS_PATH = Path(__file__).parent / "targets.json"
DEFAULT_OUTPUT_DIR = ROOT / "artifacts" / "benchmarks"

ALLOWED_RESULT_LABELS = frozenset(
    {
        "MEASURED_ADVANTAGE",
        "MEASURED_PARITY",
        "MEASURED_GAP",
        "DOCUMENTED_DIFFERENCE",
        "NOT_TESTED",
        "UNAVAILABLE",
    }
)
PRODUCT_TRUTH_LABELS = frozenset(
    {"MEASURED", "REPORTED", "MODELED", "SAMPLE", "ROADMAP", "UNAVAILABLE"}
)
DIMENSIONS = (
    ("time_to_first_source", "Time to first source"),
    ("time_to_first_useful_service_view", "Time to first useful service view"),
    (
        "time_to_map_service_to_business_outcome",
        "Time to map a service to a business outcome",
    ),
    (
        "time_to_explain_incident_with_evidence",
        "Time to explain an incident with evidence",
    ),
    ("time_to_replay_decision", "Time to replay a decision"),
    ("receipt_completeness", "Receipt completeness"),
    ("truth_label_completeness", "Truth-label completeness"),
    ("source_binding_completeness", "Source-binding completeness"),
    ("query_latency", "Query latency"),
    ("ingest_throughput", "Ingest throughput"),
    ("storage_growth", "Storage growth"),
    ("ui_keyboard_completion", "UI keyboard completion"),
    ("mobile_overflow", "Mobile overflow"),
    ("bundle_size", "Bundle size"),
    ("resource_consumption", "Resource consumption"),
)
DIMENSION_IDS = tuple(item[0] for item in DIMENSIONS)
DISPLAY_NAMES = dict(DIMENSIONS)

_SHA40 = frozenset("0123456789abcdef")
_TENANT_ID = "11111111-1111-4111-8111-111111111111"
_WORKSPACE_ID = "22222222-2222-4222-8222-222222222222"
_DEV_TOKEN = "benchmark-local-only-" + "b" * 48
_BENCHMARK_ENV_KEYS = (
    "DATABASE_URL",
    "GITHUB_SHA",
    "LYTE_DEMO_MODE",
    "LYTE_DEV_AUTH_ENABLED",
    "LYTE_DEV_AUTH_SUBJECT",
    "LYTE_DEV_AUTH_TOKEN",
    "LYTE_DEV_TENANT_ID",
    "LYTE_DEV_WORKSPACE_ID",
    "LYTE_ENV",
    "LYTE_REQUIRE_SOURCE_BINDING",
    "LYTE_SOURCE_REVISION",
    "LYTE_WEBHOOK_HMAC_SECRET",
    "OIDC_AUDIENCE",
    "OIDC_ISSUER",
    "OIDC_JWKS_URL",
    "SOURCE_REVISION",
)


@dataclass(frozen=True, slots=True)
class RunConfig:
    source_revision: str
    working_tree_state: str
    workflow_iterations: int = 5
    query_iterations: int = 30
    query_warmups: int = 5
    ingest_batches: int = 20
    spans_per_batch: int = 10
    output_dir: Path = DEFAULT_OUTPUT_DIR
    exact_command: str = ""
    browser_evidence: Path | None = None

    def __post_init__(self) -> None:
        revision = self.source_revision.strip().lower()
        if len(revision) != 40 or any(character not in _SHA40 for character in revision):
            raise ValueError("source_revision must be an exact lowercase 40-character Git SHA")
        if self.working_tree_state not in {"clean", "modified", "unknown"}:
            raise ValueError("working_tree_state must be clean, modified, or unknown")
        for name in (
            "workflow_iterations",
            "query_iterations",
            "query_warmups",
            "ingest_batches",
            "spans_per_batch",
        ):
            minimum = 0 if name == "query_warmups" else 1
            if getattr(self, name) < minimum:
                raise ValueError(f"{name} must be at least {minimum}")
        object.__setattr__(self, "source_revision", revision)
        object.__setattr__(self, "output_dir", Path(self.output_dir).resolve())
        if self.browser_evidence is not None:
            object.__setattr__(
                self,
                "browser_evidence",
                Path(self.browser_evidence).resolve(),
            )


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _read_browser_evidence(path: Path, source_revision: str) -> dict[str, Any]:
    value = _read_json(path)
    if value.get("schema") != "szl.lyte-ui-browser-evidence/v1":
        raise ValueError(f"{path} is not Lyte browser evidence v1")
    observed_revision = value.get("source_identity", {}).get("build", {}).get("revision")
    if observed_revision != source_revision:
        raise ValueError(
            "browser evidence source revision does not match benchmark source revision: "
            f"{observed_revision!r} != {source_revision!r}"
        )
    summary = value.get("summary")
    keyboard = value.get("keyboard")
    viewports = value.get("viewports")
    if not isinstance(summary, dict) or not isinstance(keyboard, dict):
        raise ValueError("browser evidence must contain summary and keyboard objects")
    if not isinstance(viewports, list) or not viewports:
        raise ValueError("browser evidence must contain non-empty viewport measurements")
    return value


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_text(value: Any) -> str:
    return (
        json.dumps(
            value,
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    )


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _percentile(values: Sequence[float], percentile: float) -> float:
    """Return a nearest-rank percentile with a declared deterministic method."""

    if not values:
        raise ValueError("percentile requires at least one sample")
    ordered = sorted(values)
    rank = max(1, math.ceil((percentile / 100.0) * len(ordered)))
    return round(float(ordered[rank - 1]), 6)


def _timing_summary(samples: Sequence[float]) -> dict[str, float | int | str]:
    if not samples:
        raise ValueError("timing summary requires at least one sample")
    return {
        "count": len(samples),
        "minimum_ms": round(min(samples), 6),
        "median_ms": _percentile(samples, 50),
        "p95_ms": _percentile(samples, 95),
        "p99_ms": _percentile(samples, 99),
        "maximum_ms": round(max(samples), 6),
        "percentile_method": "nearest_rank",
    }


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000.0, 6)


def _assert_response(response: Any, *, path: str) -> dict[str, Any]:
    if response.status_code != 200:
        raise RuntimeError(f"{path} returned HTTP {response.status_code}: {response.text[:240]}")
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError(f"{path} did not return a JSON object")
    return payload


@contextmanager
def _benchmark_environment(database_path: Path, source_revision: str) -> Iterator[None]:
    previous = {name: os.environ.get(name) for name in _BENCHMARK_ENV_KEYS}
    for name in _BENCHMARK_ENV_KEYS:
        os.environ.pop(name, None)
    os.environ.update(
        {
            "DATABASE_URL": f"sqlite+pysqlite:///{database_path.as_posix()}",
            "LYTE_DEMO_MODE": "true",
            "LYTE_DEV_AUTH_ENABLED": "true",
            "LYTE_DEV_AUTH_SUBJECT": "open-benchmark-runner",
            "LYTE_DEV_AUTH_TOKEN": _DEV_TOKEN,
            "LYTE_DEV_TENANT_ID": _TENANT_ID,
            "LYTE_DEV_WORKSPACE_ID": _WORKSPACE_ID,
            "LYTE_ENV": "test",
            "LYTE_REQUIRE_SOURCE_BINDING": "true",
            "LYTE_SOURCE_REVISION": source_revision,
        }
    )
    try:
        yield
    finally:
        for name in _BENCHMARK_ENV_KEYS:
            os.environ.pop(name, None)
        for name, value in previous.items():
            if value is not None:
                os.environ[name] = value


def _auth_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_DEV_TOKEN}",
        "X-Lyte-Tenant-ID": _TENANT_ID,
        "X-Lyte-Workspace-ID": _WORKSPACE_ID,
    }


def _page_bodies(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    items = payload.get("items")
    if not isinstance(items, list):
        return []
    return [
        item["body"]
        for item in items
        if isinstance(item, dict) and isinstance(item.get("body"), dict)
    ]


def _workflow_once(
    fixture: Mapping[str, Any],
    source_revision: str,
    database_path: Path,
) -> dict[str, float]:
    expected = fixture["expected"]
    service_id = str(expected["service_id"])
    journey_id = str(expected["journey_id"])
    outcome_id = str(expected["outcome_id"])
    incident_id = str(expected["incident_id"])
    decision_id = str(expected["decision_id"])
    started = time.perf_counter()
    with _benchmark_environment(database_path, source_revision):
        with TestClient(create_app()) as client:
            sources = _assert_response(
                client.get("/api/lyte/v2/sources"), path="/api/lyte/v2/sources"
            )
            if not sources.get("sources"):
                raise RuntimeError("source catalog is empty")
            first_source_ms = _elapsed_ms(started)

            service = _assert_response(
                client.get(f"/api/lyte/v2/services/{service_id}"),
                path=f"/api/lyte/v2/services/{service_id}",
            )
            if service.get("body", {}).get("service_id") != service_id:
                raise RuntimeError("useful service view did not contain the expected service")
            first_service_ms = _elapsed_ms(started)

            mapping_started = time.perf_counter()
            journeys = _assert_response(
                client.get("/api/lyte/v2/journeys"), path="/api/lyte/v2/journeys"
            )
            outcomes = _assert_response(
                client.get("/api/lyte/v2/outcomes"), path="/api/lyte/v2/outcomes"
            )
            journey = next(
                (body for body in _page_bodies(journeys) if body.get("journey_id") == journey_id),
                None,
            )
            outcome = next(
                (body for body in _page_bodies(outcomes) if body.get("outcome_id") == outcome_id),
                None,
            )
            mapped = bool(
                journey
                and outcome
                and service_id in journey.get("service_ids", [])
                and outcome_id in journey.get("outcome_ids", [])
                and service_id in outcome.get("service_ids", [])
                and journey_id in outcome.get("journey_ids", [])
            )
            if not mapped:
                raise RuntimeError("fixture service, journey, and outcome were not mutually mapped")
            map_ms = _elapsed_ms(mapping_started)

            explanation_started = time.perf_counter()
            incidents = _assert_response(
                client.get("/api/lyte/v2/incidents"), path="/api/lyte/v2/incidents"
            )
            incident = next(
                (
                    body
                    for body in _page_bodies(incidents)
                    if body.get("incident_id") == incident_id
                ),
                None,
            )
            answer = _assert_response(
                client.post(
                    "/api/lyte/v2/ask",
                    json={"question": str(expected["ask_question"])},
                ),
                path="/api/lyte/v2/ask",
            )
            if (
                not incident
                or not incident.get("evidence_refs")
                or not answer.get("citations")
                or answer.get("causality_claimed") is not False
            ):
                raise RuntimeError("incident explanation lacked evidence or truth boundary")
            explain_ms = _elapsed_ms(explanation_started)

            replay_started = time.perf_counter()
            decisions = _assert_response(
                client.get("/api/lyte/v2/decisions"), path="/api/lyte/v2/decisions"
            )
            playback = _assert_response(
                client.get("/api/lyte/v2/playback"), path="/api/lyte/v2/playback"
            )
            decision = next(
                (
                    item
                    for item in decisions.get("items", [])
                    if isinstance(item, dict) and item.get("entity_id") == decision_id
                ),
                None,
            )
            frames = playback.get("items", [])
            if (
                decision is None
                or not isinstance(frames, list)
                or not frames
                or not all(
                    isinstance(frame, dict) and frame.get("evidence_refs") for frame in frames
                )
                or playback.get("production_action_claimed") is not False
            ):
                raise RuntimeError("decision playback was incomplete or claimed production action")
            replay_ms = _elapsed_ms(replay_started)
    return {
        "time_to_first_source": first_source_ms,
        "time_to_first_useful_service_view": first_service_ms,
        "time_to_map_service_to_business_outcome": map_ms,
        "time_to_explain_incident_with_evidence": explain_ms,
        "time_to_replay_decision": replay_ms,
    }


def _measure_workflows(
    fixture: Mapping[str, Any], config: RunConfig, temporary_root: Path
) -> dict[str, list[float]]:
    samples = {dimension: [] for dimension in DIMENSION_IDS[:5]}
    for iteration in range(config.workflow_iterations):
        database_path = temporary_root / f"workflow-{iteration:03d}.sqlite3"
        observed = _workflow_once(fixture, config.source_revision, database_path)
        for dimension, value in observed.items():
            samples[dimension].append(value)
    return samples


def _build_otlp_payload(
    fixture: Mapping[str, Any], *, batch_index: int, spans_per_batch: int
) -> bytes:
    otlp = fixture["otlp"]
    template = otlp["span_template"]
    spans: list[dict[str, Any]] = []
    for span_index in range(spans_per_batch):
        sequence = batch_index * spans_per_batch + span_index + 1
        span = copy.deepcopy(template)
        span["traceId"] = f"{sequence:032x}"
        span["spanId"] = f"{sequence:016x}"
        start = 1_000_000_000 + sequence * 3_000_000
        span["startTimeUnixNano"] = str(start)
        span["endTimeUnixNano"] = str(start + 2_500_000)
        spans.append(span)
    payload = {
        "resourceSpans": [
            {
                "resource": {"attributes": otlp["resource_attributes"]},
                "scopeSpans": [{"scope": otlp["scope"], "spans": spans}],
            }
        ]
    }
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _database_bytes(path: Path) -> int:
    candidates = (path, Path(f"{path}-wal"))
    return sum(candidate.stat().st_size for candidate in candidates if candidate.exists())


def _query_payloads(client: TestClient, fixture: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    service_id = str(fixture["expected"]["service_id"])
    question = str(fixture["expected"]["ask_question"])
    routes = {
        "build": "/api/build-info",
        "sources": "/api/lyte/v2/sources",
        "services": "/api/lyte/v2/services",
        "service": f"/api/lyte/v2/services/{service_id}",
        "journeys": "/api/lyte/v2/journeys",
        "outcomes": "/api/lyte/v2/outcomes",
        "agents": "/api/lyte/v2/agents",
        "incidents": "/api/lyte/v2/incidents",
        "decisions": "/api/lyte/v2/decisions",
        "playback": "/api/lyte/v2/playback",
        "evidence": "/api/lyte/v2/evidence",
        "receipts": "/api/lyte/v2/receipts",
    }
    payloads = {
        name: _assert_response(client.get(path), path=path) for name, path in routes.items()
    }
    payloads["ask"] = _assert_response(
        client.post("/api/lyte/v2/ask", json={"question": question}),
        path="/api/lyte/v2/ask",
    )
    return payloads


def _truth_contract_points(
    payloads: Mapping[str, Mapping[str, Any]],
) -> list[tuple[str, Mapping[str, Any]]]:
    points: list[tuple[str, Mapping[str, Any]]] = []

    def add(path: str, value: Any) -> None:
        if isinstance(value, Mapping):
            points.append((path, value))
        else:
            points.append((path, {}))

    for name in (
        "build",
        "sources",
        "services",
        "service",
        "journeys",
        "outcomes",
        "agents",
        "incidents",
        "decisions",
        "playback",
        "evidence",
        "receipts",
        "ask",
    ):
        add(name, payloads[name])

    for index, source in enumerate(payloads["sources"].get("sources", [])):
        add(f"sources.sources[{index}]", source)

    page_names = (
        "services",
        "journeys",
        "outcomes",
        "agents",
        "incidents",
        "decisions",
        "playback",
        "evidence",
        "receipts",
    )
    body_label_kinds = {
        "Service",
        "CustomerJourney",
        "BusinessOutcome",
        "AgentTraceSummary",
        "Incident",
        "ReplaySnapshot",
        "EvidenceReference",
        "ActionRequest",
    }
    for page_name in page_names:
        items = payloads[page_name].get("items", [])
        for index, item in enumerate(items if isinstance(items, list) else []):
            item_path = f"{page_name}.items[{index}]"
            add(item_path, item)
            if not isinstance(item, Mapping):
                continue
            body = item.get("body")
            if item.get("entity_kind") in body_label_kinds:
                add(f"{item_path}.body", body)
            if not isinstance(body, Mapping):
                continue
            indicators = body.get("indicators")
            if isinstance(indicators, Mapping):
                for indicator_name, indicator in indicators.items():
                    add(f"{item_path}.body.indicators.{indicator_name}", indicator)
            steps = body.get("steps")
            if isinstance(steps, list):
                for step_index, step in enumerate(steps):
                    step_path = f"{item_path}.body.steps[{step_index}]"
                    add(step_path, step)
                    if isinstance(step, Mapping) and isinstance(step.get("indicators"), Mapping):
                        for indicator_name, indicator in step["indicators"].items():
                            add(f"{step_path}.indicators.{indicator_name}", indicator)

    service_body = payloads["service"].get("body")
    add("service.body", service_body)
    if isinstance(service_body, Mapping) and isinstance(service_body.get("indicators"), Mapping):
        for indicator_name, indicator in service_body["indicators"].items():
            add(f"service.body.indicators.{indicator_name}", indicator)

    for index, citation in enumerate(payloads["ask"].get("citations", [])):
        add(f"ask.citations[{index}]", citation)
    for index, formula in enumerate(payloads["ask"].get("formulas", [])):
        add(f"ask.formulas[{index}]", formula)
    return points


def _measure_truth_completeness(
    payloads: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    points = _truth_contract_points(payloads)
    missing: list[str] = []
    invalid: list[dict[str, str]] = []
    observed_labels: Counter[str] = Counter()
    for path, value in points:
        label = value.get("truth_label")
        if not isinstance(label, str) or not label:
            missing.append(path)
        elif label not in PRODUCT_TRUTH_LABELS:
            invalid.append({"path": path, "value": label})
        else:
            observed_labels[label] += 1
    complete = len(points) - len(missing) - len(invalid)
    percentage = (complete / len(points) * 100.0) if points else 0.0
    return {
        "numerator": complete,
        "denominator": len(points),
        "percentage": round(percentage, 6),
        "missing_paths": missing,
        "invalid_labels": invalid,
        "observed_product_truth_labels": dict(sorted(observed_labels.items())),
        "contract": "explicit response, record, body, indicator, citation, and formula points",
    }


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in _SHA40 for character in value)
    )


def _measure_receipt_completeness(receipts_payload: Mapping[str, Any]) -> dict[str, Any]:
    items = receipts_payload.get("items", [])
    receipts = items if isinstance(items, list) else []
    missing: list[dict[str, Any]] = []
    valid_checks = 0
    checks_per_receipt = 14
    previous_hash: str | None = None
    for index, receipt in enumerate(receipts):
        if not isinstance(receipt, Mapping):
            missing.append({"index": index, "checks": ["receipt_object"]})
            previous_hash = None
            continue
        checks = {
            "schema": receipt.get("schema") == "szl.lyte.receipt-view/v1",
            "id": isinstance(receipt.get("id"), str) and bool(receipt.get("id")),
            "kind": isinstance(receipt.get("kind"), str) and bool(receipt.get("kind")),
            "subject_type": isinstance(receipt.get("subject_type"), str)
            and bool(receipt.get("subject_type")),
            "subject_id": isinstance(receipt.get("subject_id"), str)
            and bool(receipt.get("subject_id")),
            "truth_label": receipt.get("truth_label") in PRODUCT_TRUTH_LABELS,
            "payload": isinstance(receipt.get("payload"), Mapping),
            "payload_sha256": _is_sha256(receipt.get("payload_sha256")),
            "payload_digest_matches": isinstance(receipt.get("payload"), Mapping)
            and receipt.get("payload_sha256") == sha256_json(receipt["payload"]),
            "evidence_refs": isinstance(receipt.get("evidence_refs"), list),
            "sequence": receipt.get("sequence") == index + 1,
            "record_hash": _is_sha256(receipt.get("record_hash")),
            "previous_hash": receipt.get("previous_hash") == previous_hash,
            "created_at": isinstance(receipt.get("created_at"), str)
            and str(receipt.get("created_at")).endswith("Z"),
        }
        failed = [name for name, passed in checks.items() if not passed]
        valid_checks += checks_per_receipt - len(failed)
        if failed:
            missing.append({"index": index, "sequence": receipt.get("sequence"), "checks": failed})
        previous_hash = (
            str(receipt["record_hash"]) if _is_sha256(receipt.get("record_hash")) else None
        )
    denominator = len(receipts) * checks_per_receipt
    percentage = (valid_checks / denominator * 100.0) if denominator else 0.0
    return {
        "numerator": valid_checks,
        "denominator": denominator,
        "percentage": round(percentage, 6),
        "receipt_count": len(receipts),
        "checks_per_receipt": checks_per_receipt,
        "missing_or_invalid": missing,
        "chain_head": previous_hash,
        "append_only_declared": receipts_payload.get("append_only") is True,
    }


def _measure_source_binding(build: Mapping[str, Any], expected_revision: str) -> dict[str, Any]:
    source = build.get("source_binding")
    binding = source if isinstance(source, Mapping) else {}
    build_identity = build.get("build")
    build_data = build_identity if isinstance(build_identity, Mapping) else {}
    checks = {
        "source_repository_canonical": build.get("source_repository")
        == "szl-holdings/lyte-services",
        "source_revision_exact": build.get("source_revision") == expected_revision,
        "runtime_repository_canonical": build.get("runtime_repository")
        == "szl-holdings/lyte-services",
        "runtime_source_revision_exact": build.get("runtime_source_revision")
        == expected_revision,
        "effectors_disabled": build.get("effectors_enabled") is False,
        "human_approval_required": build.get("human_approval_required") is True,
        "build_state_observed": build_data.get("state") == "OBSERVED",
        "build_revision_exact": build_data.get("revision") == expected_revision,
        "build_repository_canonical": build_data.get("repository") == "szl-holdings/lyte-services",
        "bindings_agree": binding.get("bindings_agree") is True,
        "product_repository_canonical": binding.get("product_repository")
        == "szl-holdings/lyte-services",
        "product_revision_exact": binding.get("product_revision") == expected_revision,
        "hub_surface_canonical": binding.get("hub_surface") == "SZLHOLDINGS/lyte",
        "evidence_source_present": isinstance(binding.get("evidence_sources"), list)
        and bool(binding.get("evidence_sources")),
        "invalid_sources_empty": binding.get("invalid_sources") == [],
        "one_distinct_revision": binding.get("distinct_revision_count") == 1,
        "build_truth_measured": build.get("truth_label") == "MEASURED",
    }
    passed = sum(checks.values())
    denominator = len(checks)
    return {
        "numerator": passed,
        "denominator": denominator,
        "percentage": round(passed / denominator * 100.0, 6),
        "checks": checks,
        "missing_or_mismatched": [name for name, result in checks.items() if not result],
        "observed_revision": build_data.get("revision"),
        "expected_revision": expected_revision,
    }


def _measure_main_runtime(
    fixture: Mapping[str, Any], config: RunConfig, database_path: Path
) -> dict[str, Any]:
    query_samples: list[float] = []
    ingest_samples: list[float] = []
    payload_bytes = 0
    accepted_records = 0
    rejected_batches: list[dict[str, Any]] = []
    with _benchmark_environment(database_path, config.source_revision):
        with TestClient(create_app()) as client:
            payloads = _query_payloads(client, fixture)
            service_path = f"/api/lyte/v2/services/{fixture['expected']['service_id']}"
            for _ in range(config.query_warmups):
                _assert_response(client.get(service_path), path=service_path)
            for _ in range(config.query_iterations):
                started = time.perf_counter()
                _assert_response(client.get(service_path), path=service_path)
                query_samples.append(_elapsed_ms(started))

            before_storage_bytes = _database_bytes(database_path)
            for batch_index in range(config.ingest_batches):
                body = _build_otlp_payload(
                    fixture,
                    batch_index=batch_index,
                    spans_per_batch=config.spans_per_batch,
                )
                payload_bytes += len(body)
                headers = {
                    **_auth_headers(),
                    "Content-Type": "application/json",
                    "Idempotency-Key": f"open-benchmark-{batch_index:08d}",
                }
                started = time.perf_counter()
                response = client.post(
                    "/api/lyte/v2/ingest/otlp",
                    content=body,
                    headers=headers,
                )
                ingest_samples.append(_elapsed_ms(started))
                if response.status_code == 200:
                    payload = response.json()
                    accepted_records += int(payload.get("record_count", 0))
                else:
                    rejected_batches.append(
                        {
                            "batch_index": batch_index,
                            "status_code": response.status_code,
                            "body_prefix": response.text[:160],
                        }
                    )
            after_storage_bytes = _database_bytes(database_path)
            receipts_payload = _assert_response(
                client.get("/api/lyte/v2/receipts?limit=100"),
                path="/api/lyte/v2/receipts?limit=100",
            )
            payloads["receipts"] = receipts_payload

    elapsed_seconds = sum(ingest_samples) / 1000.0
    storage_delta = after_storage_bytes - before_storage_bytes
    return {
        "query_samples_ms": query_samples,
        "query_summary": _timing_summary(query_samples),
        "ingest_samples_ms": ingest_samples,
        "ingest_elapsed_seconds": round(elapsed_seconds, 9),
        "payload_bytes": payload_bytes,
        "accepted_records": accepted_records,
        "accepted_batches": config.ingest_batches - len(rejected_batches),
        "rejected_batches": rejected_batches,
        "records_per_second": round(
            accepted_records / elapsed_seconds if elapsed_seconds > 0 else 0.0,
            6,
        ),
        "storage_before_bytes": before_storage_bytes,
        "storage_after_bytes": after_storage_bytes,
        "storage_delta_bytes": storage_delta,
        "storage_bytes_per_record": round(
            storage_delta / accepted_records if accepted_records > 0 else 0.0,
            6,
        ),
        "receipt_completeness": _measure_receipt_completeness(receipts_payload),
        "truth_completeness": _measure_truth_completeness(payloads),
        "source_binding": _measure_source_binding(payloads["build"], config.source_revision),
    }


def _measure_bundle() -> dict[str, Any]:
    paths = (
        ROOT / "lyte" / "ui" / "index.html",
        ROOT / "lyte" / "ui" / "styles.css",
        ROOT / "lyte" / "ui" / "app.js",
    )
    files: list[dict[str, Any]] = []
    for path in paths:
        content = path.read_bytes()
        compressed = gzip.compress(content, compresslevel=9, mtime=0)
        files.append(
            {
                "path": path.relative_to(ROOT).as_posix(),
                "raw_bytes": len(content),
                "gzip_bytes": len(compressed),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
    return {
        "scope": "first-party frontend HTML, CSS, and JavaScript; dependencies and container excluded",
        "files": files,
        "raw_bytes": sum(item["raw_bytes"] for item in files),
        "gzip_bytes": sum(item["gzip_bytes"] for item in files),
        "compression": "gzip level 9 with mtime=0",
    }


def _peak_rss_bytes() -> tuple[int | None, str]:
    """Return process-lifetime peak RSS without adding a benchmark dependency."""

    if os.name == "nt":
        try:
            from ctypes import wintypes

            class ProcessMemoryCounters(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            counters = ProcessMemoryCounters()
            counters.cb = ctypes.sizeof(counters)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            psapi = ctypes.WinDLL("psapi", use_last_error=True)
            kernel32.GetCurrentProcess.argtypes = []
            kernel32.GetCurrentProcess.restype = wintypes.HANDLE
            psapi.GetProcessMemoryInfo.argtypes = [
                wintypes.HANDLE,
                ctypes.POINTER(ProcessMemoryCounters),
                wintypes.DWORD,
            ]
            psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
            handle = kernel32.GetCurrentProcess()
            succeeded = psapi.GetProcessMemoryInfo(
                handle,
                ctypes.byref(counters),
                counters.cb,
            )
            if not succeeded:
                return None, "Windows GetProcessMemoryInfo failed"
            return int(counters.PeakWorkingSetSize), "Windows PeakWorkingSetSize"
        except (AttributeError, OSError, ValueError) as exc:
            return None, f"Windows peak RSS unavailable: {type(exc).__name__}"
    try:
        import resource

        usage = resource.getrusage(resource.RUSAGE_SELF)
        value = float(usage.ru_maxrss)
        multiplier = 1 if sys.platform == "darwin" else 1024
        return int(value * multiplier), "getrusage process-lifetime ru_maxrss"
    except (ImportError, OSError, ValueError) as exc:
        return None, f"POSIX peak RSS unavailable: {type(exc).__name__}"


def _target_passes(value: float, target: Mapping[str, Any]) -> bool:
    operator = target.get("operator")
    threshold = float(target["value"])
    if operator == "lte":
        return value <= threshold
    if operator == "gte":
        return value >= threshold
    if operator == "eq":
        return value == threshold
    raise ValueError(f"unsupported target operator: {operator}")


def _measured_label(value: float, target: Mapping[str, Any]) -> str:
    return "MEASURED_PARITY" if _target_passes(value, target) else "MEASURED_GAP"


def _target_text(target: Mapping[str, Any]) -> str:
    operator = {
        "lte": "<=",
        "gte": ">=",
        "eq": "=",
        "all_lte": "all <=",
    }.get(str(target.get("operator")), str(target.get("operator")))
    if target.get("operator") == "all_lte":
        values = target.get("values", {})
        return f"{operator} {json.dumps(values, sort_keys=True)}"
    return f"{operator} {target.get('value')} {target.get('unit')} ({target.get('statistic')})"


def _result(
    dimension: str,
    *,
    result_label: str,
    measurement: Mapping[str, Any] | None,
    raw_measurements: Any,
    target: Mapping[str, Any],
    evidence: Sequence[str],
    limitations: Sequence[str],
) -> dict[str, Any]:
    if result_label not in ALLOWED_RESULT_LABELS:
        raise ValueError(f"unsupported benchmark result label: {result_label}")
    if dimension not in DIMENSION_IDS:
        raise ValueError(f"unsupported benchmark dimension: {dimension}")
    if result_label in {"NOT_TESTED", "UNAVAILABLE"} and measurement is not None:
        raise ValueError(f"{result_label} cannot contain a numeric measurement")
    return {
        "id": dimension,
        "dimension": DISPLAY_NAMES[dimension],
        "result_label": result_label,
        "comparison_subject": "predeclared open Lyte acceptance gate",
        "comparison_basis": {
            "kind": "OPEN_ACCEPTANCE_GATE",
            "target": dict(target),
            "external_vendor_baseline": "NOT_TESTED",
        },
        "measurement": dict(measurement) if measurement is not None else None,
        "raw_measurements": raw_measurements,
        "evidence": list(evidence),
        "limitations": list(limitations),
    }


def _browser_results(
    browser_evidence: Mapping[str, Any] | None,
    target_rows: Mapping[str, Any],
) -> list[dict[str, Any]]:
    if browser_evidence is None:
        browser_not_tested = {
            "browser": None,
            "browser_version": None,
            "viewport": None,
            "numeric_value": None,
        }
        return [
            _result(
                "ui_keyboard_completion",
                result_label="NOT_TESTED",
                measurement=None,
                raw_measurements={
                    **browser_not_tested,
                    "required_sequence": [
                        "Tab to scene navigation",
                        "activate Services",
                        "Tab to Signal Lattice",
                        "use Arrow keys among nodes",
                        "activate a node",
                        "reach and close the evidence drawer",
                    ],
                    "reached": None,
                    "focus_order": [],
                },
                target=target_rows["ui_keyboard_completion"],
                evidence=["tests/test_frontend_contract.py contains static checks only"],
                limitations=[
                    "No real browser was launched by this pure-Python run; static source is not treated as interaction completion evidence."
                ],
            ),
            _result(
                "mobile_overflow",
                result_label="NOT_TESTED",
                measurement=None,
                raw_measurements={
                    **browser_not_tested,
                    "declared_viewports_css_px": [
                        [320, 568],
                        [375, 812],
                        [430, 932],
                        [768, 1024],
                        [1024, 768],
                        [1440, 900],
                        [1920, 1080],
                    ],
                    "scroll_width_samples": [],
                    "client_width_samples": [],
                    "overflow_px": None,
                },
                target=target_rows["mobile_overflow"],
                evidence=["tests/test_frontend_contract.py contains static checks only"],
                limitations=[
                    "No browser layout engine was launched; CSS declarations cannot prove rendered overflow behavior."
                ],
            ),
        ]

    keyboard = browser_evidence["keyboard"]
    summary = browser_evidence["summary"]
    viewports = browser_evidence["viewports"]
    browser = browser_evidence.get("browser", {})
    artifact_path = str(browser_evidence.get("_artifact_path", "browser evidence JSON"))
    screenshots = [
        str(viewport.get("screenshot")) for viewport in viewports if viewport.get("screenshot")
    ]
    limitations = [str(item) for item in browser_evidence.get("limitations", [])]
    keyboard_value = 1.0 if keyboard.get("completed") is True else 0.0
    overflow_value = float(summary["maximum_horizontal_overflow_px"])
    return [
        _result(
            "ui_keyboard_completion",
            result_label=_measured_label(
                keyboard_value,
                target_rows["ui_keyboard_completion"],
            ),
            measurement={
                "value": keyboard_value,
                "unit": "completed",
                "statistic": "all_declared_sequences",
                "browser": browser.get("product"),
                "viewport": keyboard.get("viewport"),
            },
            raw_measurements=keyboard,
            target=target_rows["ui_keyboard_completion"],
            evidence=[artifact_path, *screenshots],
            limitations=limitations,
        ),
        _result(
            "mobile_overflow",
            result_label=_measured_label(
                overflow_value,
                target_rows["mobile_overflow"],
            ),
            measurement={
                "value": overflow_value,
                "unit": "overflow_px",
                "statistic": "maximum",
                "browser": browser.get("product"),
                "viewport_count": len(viewports),
            },
            raw_measurements={
                "viewports": viewports,
                "local_assets_only": summary.get("local_assets_only"),
            },
            target=target_rows["mobile_overflow"],
            evidence=[artifact_path, *screenshots],
            limitations=limitations,
        ),
    ]


def _build_results(
    workflow_samples: Mapping[str, Sequence[float]],
    runtime: Mapping[str, Any],
    bundle: Mapping[str, Any],
    resource_measurement: Mapping[str, Any],
    targets: Mapping[str, Any],
    browser_evidence: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    target_rows = targets["targets"]
    common_runtime_limits = [
        "In-process FastAPI TestClient and local SQLite are measured; network, proxy, and PostgreSQL costs are excluded.",
        "The dataset is the public synthetic SAMPLE fixture, not production telemetry.",
    ]
    workflow_evidence = [
        "benchmarks/observability/fixtures/checkout-observability-v1.json",
        "lyte/demo.py",
        "lyte/api/routes_entities.py",
        "lyte/api/routes_ask.py",
    ]
    boundaries = {
        "time_to_first_source": "timer starts before application lifespan startup and stops after a non-empty source catalog is validated",
        "time_to_first_useful_service_view": "timer starts before application lifespan startup and stops after checkout-api detail is validated",
        "time_to_map_service_to_business_outcome": "timer covers journey and outcome retrieval plus bidirectional service/journey/outcome ID validation",
        "time_to_explain_incident_with_evidence": "timer covers incident retrieval and deterministic Ask Lyte response validation for citations and no causal claim",
        "time_to_replay_decision": "timer covers decision and playback retrieval plus evidence and non-execution validation",
    }
    for dimension in DIMENSION_IDS[:5]:
        samples = list(workflow_samples[dimension])
        summary = _timing_summary(samples)
        target = target_rows[dimension]
        results.append(
            _result(
                dimension,
                result_label=_measured_label(float(summary["p95_ms"]), target),
                measurement={
                    "value": summary["p95_ms"],
                    "unit": "ms",
                    "statistic": "p95",
                    "summary": summary,
                    "boundary": boundaries[dimension],
                },
                raw_measurements={"elapsed_ms": samples},
                target=target,
                evidence=workflow_evidence,
                limitations=common_runtime_limits,
            )
        )

    completeness_specs = (
        ("receipt_completeness", "receipt_completeness", "artifacts are evaluated after ingest"),
        (
            "truth_label_completeness",
            "truth_completeness",
            "only explicitly enumerated benchmark response contract points are in the denominator",
        ),
        (
            "source_binding_completeness",
            "source_binding",
            "the exact source revision is injected into the isolated test runtime and does not prove a deployed artifact",
        ),
    )
    for dimension, runtime_key, limitation in completeness_specs:
        raw = runtime[runtime_key]
        target = target_rows[dimension]
        percentage = float(raw["percentage"])
        results.append(
            _result(
                dimension,
                result_label=_measured_label(percentage, target),
                measurement={
                    "value": percentage,
                    "unit": "percent",
                    "statistic": "coverage",
                    "numerator": raw["numerator"],
                    "denominator": raw["denominator"],
                },
                raw_measurements=raw,
                target=target,
                evidence=[
                    "lyte/api/responses.py",
                    "lyte/api/routes_health.py",
                    "lyte/domain/receipts.py",
                    "runtime API responses from the open fixture",
                ],
                limitations=[*common_runtime_limits, limitation],
            )
        )

    query_target = target_rows["query_latency"]
    query_summary = runtime["query_summary"]
    results.append(
        _result(
            "query_latency",
            result_label=_measured_label(float(query_summary["p95_ms"]), query_target),
            measurement={
                "value": query_summary["p95_ms"],
                "unit": "ms",
                "statistic": "p95",
                "route": "/api/lyte/v2/services/checkout-api",
                "summary": query_summary,
            },
            raw_measurements={"elapsed_ms": runtime["query_samples_ms"]},
            target=query_target,
            evidence=["lyte/api/routes_entities.py", "runtime API responses from the open fixture"],
            limitations=common_runtime_limits,
        )
    )

    ingest_target = target_rows["ingest_throughput"]
    accepted_records = int(runtime["accepted_records"])
    if accepted_records:
        ingest_label = _measured_label(float(runtime["records_per_second"]), ingest_target)
        ingest_measurement: Mapping[str, Any] | None = {
            "value": runtime["records_per_second"],
            "unit": "records/s",
            "statistic": "accepted_records / summed_request_elapsed_seconds",
            "accepted_records": accepted_records,
            "accepted_batches": runtime["accepted_batches"],
            "rejected_batches": len(runtime["rejected_batches"]),
            "payload_bytes": runtime["payload_bytes"],
            "elapsed_seconds": runtime["ingest_elapsed_seconds"],
        }
    else:
        ingest_label = "UNAVAILABLE"
        ingest_measurement = None
    results.append(
        _result(
            "ingest_throughput",
            result_label=ingest_label,
            measurement=ingest_measurement,
            raw_measurements={
                "request_elapsed_ms": runtime["ingest_samples_ms"],
                "rejected_batches": runtime["rejected_batches"],
            },
            target=ingest_target,
            evidence=[
                "benchmarks/observability/fixtures/checkout-observability-v1.json",
                "lyte/connectors/otlp_http.py",
                "lyte/api/routes_ingest.py",
            ],
            limitations=common_runtime_limits,
        )
    )

    storage_target = target_rows["storage_growth"]
    if accepted_records:
        storage_value = float(runtime["storage_bytes_per_record"])
        storage_label = _measured_label(storage_value, storage_target)
        storage_measurement: Mapping[str, Any] | None = {
            "value": storage_value,
            "unit": "bytes/record",
            "statistic": "database file delta / accepted normalized spans",
            "baseline_bytes": runtime["storage_before_bytes"],
            "final_bytes": runtime["storage_after_bytes"],
            "delta_bytes": runtime["storage_delta_bytes"],
            "record_count": accepted_records,
        }
    else:
        storage_label = "UNAVAILABLE"
        storage_measurement = None
    results.append(
        _result(
            "storage_growth",
            result_label=storage_label,
            measurement=storage_measurement,
            raw_measurements={
                "baseline_bytes": runtime["storage_before_bytes"],
                "final_bytes": runtime["storage_after_bytes"],
                "delta_bytes": runtime["storage_delta_bytes"],
                "accepted_records": accepted_records,
            },
            target=storage_target,
            evidence=["temporary SQLite benchmark database", "lyte/persistence/models.py"],
            limitations=[
                *common_runtime_limits,
                "SQLite page allocation and receipt/projection overhead are included; PostgreSQL storage behavior is not inferred.",
            ],
        )
    )

    results.extend(_browser_results(browser_evidence, target_rows))

    bundle_target = target_rows["bundle_size"]
    results.append(
        _result(
            "bundle_size",
            result_label=_measured_label(float(bundle["gzip_bytes"]), bundle_target),
            measurement={
                "value": bundle["gzip_bytes"],
                "unit": "gzip_bytes",
                "statistic": "total",
                "raw_bytes": bundle["raw_bytes"],
                "scope": bundle["scope"],
            },
            raw_measurements=bundle,
            target=bundle_target,
            evidence=[item["path"] for item in bundle["files"]],
            limitations=[
                "Dependency closure and container image layers are outside this frontend bundle scope."
            ],
        )
    )

    resource_target = target_rows["resource_consumption"]
    resource_values = resource_target["values"]
    peak_rss = resource_measurement.get("peak_rss_bytes")
    if isinstance(peak_rss, int):
        resource_passes = (
            peak_rss <= float(resource_values["peak_rss_bytes"])
            and float(resource_measurement["cpu_time_seconds"])
            <= float(resource_values["cpu_time_seconds"])
            and float(resource_measurement["wall_time_seconds"])
            <= float(resource_values["wall_time_seconds"])
        )
        resource_label = "MEASURED_PARITY" if resource_passes else "MEASURED_GAP"
        resource_output: Mapping[str, Any] | None = resource_measurement
    else:
        resource_label = "UNAVAILABLE"
        resource_output = None
    results.append(
        _result(
            "resource_consumption",
            result_label=resource_label,
            measurement=resource_output,
            raw_measurements=resource_measurement,
            target=resource_target,
            evidence=["operating-system process counters", "Python process_time and perf_counter"],
            limitations=[
                "Peak RSS is process-lifetime high-water memory, not an isolated per-request sample.",
                "CPU and wall time include benchmark orchestration, application startup, queries, and ingest; host background load is not controlled.",
                "Container and whole-host resources are not inferred from this process measurement.",
            ],
        )
    )
    return results


def _environment_record(
    browser_evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    browser = browser_evidence.get("browser", {}) if browser_evidence else {}
    return {
        "operating_system": platform.system(),
        "operating_system_release": platform.release(),
        "architecture": platform.machine(),
        "processor": platform.processor() or None,
        "logical_cpu_count": os.cpu_count(),
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "sqlite_version": sqlite3.sqlite_version,
        "database_backend": "SQLite file",
        "http_execution": "FastAPI TestClient in process",
        "browser_name": browser.get("product"),
        "browser_revision": browser.get("revision"),
        "browser_user_agent": browser.get("user_agent"),
    }


def _build_document(
    *,
    config: RunConfig,
    fixture: Mapping[str, Any],
    fixture_hash: str,
    targets: Mapping[str, Any],
    targets_hash: str,
    results: Sequence[Mapping[str, Any]],
    browser_evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    label_counts = Counter(str(result["result_label"]) for result in results)
    run_basis = {
        "source_revision": config.source_revision,
        "working_tree_state": config.working_tree_state,
        "fixture_sha256": fixture_hash,
        "targets_sha256": targets_hash,
        "workflow_iterations": config.workflow_iterations,
        "query_iterations": config.query_iterations,
        "query_warmups": config.query_warmups,
        "ingest_batches": config.ingest_batches,
        "spans_per_batch": config.spans_per_batch,
        "browser_evidence_sha256": (browser_evidence.get("_sha256") if browser_evidence else None),
    }
    return {
        "schema": "szl.lyte.observability-benchmark/v1",
        "benchmark_version": "1.0.0",
        "run_id": hashlib.sha256(
            json.dumps(run_basis, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "generated_at": _now(),
        "subject": {
            "product": "Lyte Enterprise Signal Lattice",
            "repository": "szl-holdings/lyte-services",
            "source_revision": config.source_revision,
            "working_tree_state": config.working_tree_state,
            "source_revision_scope": (
                "declared code baseline; working_tree_state discloses whether the benchmark "
                "runner and product files are fully represented by that commit"
            ),
            "hub_surface": "SZLHOLDINGS/lyte",
        },
        "fixture": {
            "id": fixture["fixture_id"],
            "version": fixture["fixture_version"],
            "schema": fixture["schema"],
            "license": fixture["license"],
            "path": FIXTURE_PATH.relative_to(ROOT).as_posix(),
            "sha256": fixture_hash,
            "data_mode": fixture["data_mode"],
            "contains_production_data": False,
            "contains_vendor_output": False,
        },
        "methodology": {
            "mode": "vendor-neutral open-fixture measurement",
            "exact_command": config.exact_command,
            "working_directory": "repository root",
            "workflow_iterations": config.workflow_iterations,
            "query_warmups": config.query_warmups,
            "query_iterations": config.query_iterations,
            "ingest_batches": config.ingest_batches,
            "spans_per_batch": config.spans_per_batch,
            "targets_path": TARGETS_PATH.relative_to(ROOT).as_posix(),
            "targets_sha256": targets_hash,
            "targets_version": targets["target_version"],
            "targets_declared_before_run": True,
            "comparison_scope": (
                "Lyte is evaluated against committed open acceptance gates. No external vendor "
                "runtime or proprietary dataset was exercised."
            ),
            "result_label_policy": targets["label_policy"],
            "percentile_method": "nearest_rank",
        },
        "environment": _environment_record(browser_evidence),
        "summary": {
            "dimension_count": len(results),
            "result_label_counts": dict(sorted(label_counts.items())),
            "all_dimensions_present": tuple(result["id"] for result in results) == DIMENSION_IDS,
            "external_vendor_systems_exercised": [],
            "competitor_superiority_claimed": False,
            "measured_advantage_claimed": any(
                result["result_label"] == "MEASURED_ADVANTAGE" for result in results
            ),
        },
        "results": list(results),
        "overall_limitations": [
            "This is a local in-process benchmark over an explicitly SAMPLE fixture; it is not a production load test.",
            "SQLite results do not establish PostgreSQL, distributed, container, or hosted-service performance.",
            "No external vendor runtime was tested, so this report makes no comparative superiority claim.",
            (
                "Browser-dependent keyboard and overflow procedures were not executed and remain NOT_TESTED."
                if browser_evidence is None
                else "Browser evidence covers the recorded Chromium build and declared viewports; other browser engines are not inferred."
            ),
            "Host background load was not isolated; raw samples are retained so another environment can rerun the same protocol.",
            (
                "When working_tree_state is modified or unknown, the source revision identifies "
                "the baseline only and does not bind uncommitted changes."
            ),
        ],
        "artifact_generation": {
            "canonical_artifact": "artifacts/benchmarks/lyte-benchmark.json",
            "derived_artifacts": [
                "artifacts/benchmarks/lyte-benchmark.md",
                "artifacts/benchmarks/lyte-capability-matrix.csv",
            ],
            "generated_from_one_in_memory_document": True,
            "secret_values_recorded": False,
        },
    }


def validate_document(document: Mapping[str, Any]) -> None:
    results = document.get("results")
    if not isinstance(results, list):
        raise ValueError("benchmark results must be a list")
    ids = tuple(result.get("id") for result in results if isinstance(result, Mapping))
    if ids != DIMENSION_IDS:
        raise ValueError(f"benchmark results must contain all dimensions in order: {DIMENSION_IDS}")
    for result in results:
        if not isinstance(result, Mapping):
            raise ValueError("each benchmark result must be an object")
        label = result.get("result_label")
        if label not in ALLOWED_RESULT_LABELS:
            raise ValueError(f"unsupported benchmark result label: {label}")
        if label in {"NOT_TESTED", "UNAVAILABLE"} and result.get("measurement") is not None:
            raise ValueError(f"{label} result {result.get('id')} must use a null measurement")
    if document.get("summary", {}).get("competitor_superiority_claimed") is not False:
        raise ValueError("benchmark must not claim competitor superiority")
    if document.get("summary", {}).get("measured_advantage_claimed") is not False:
        raise ValueError("this Lyte-only runner cannot produce MEASURED_ADVANTAGE")


def _markdown_value(result: Mapping[str, Any]) -> str:
    measurement = result.get("measurement")
    if not isinstance(measurement, Mapping):
        return "not measured"
    value = measurement.get("value")
    unit = measurement.get("unit")
    if value is not None:
        return f"{value} {unit}"
    if result.get("id") == "resource_consumption":
        peak = measurement.get("peak_rss_bytes")
        cpu = measurement.get("cpu_time_seconds")
        wall = measurement.get("wall_time_seconds")
        return f"peak RSS {peak} bytes; CPU {cpu} s; wall {wall} s"
    return "measured; see raw record"


def _markdown_escape(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_markdown(document: Mapping[str, Any]) -> str:
    fixture = document["fixture"]
    methodology = document["methodology"]
    subject = document["subject"]
    lines = [
        "# Lyte open observability benchmark",
        "",
        (
            "This report measures Lyte against committed open acceptance gates. It did not "
            "exercise a competitor, scrape a proprietary service, or infer superiority from "
            "features or visual opinion."
        ),
        "",
        "## Provenance",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Run ID | `{document['run_id']}` |",
        f"| Generated | `{document['generated_at']}` |",
        f"| Source revision | `{subject['source_revision']}` |",
        f"| Working tree | `{subject['working_tree_state']}` |",
        f"| Fixture | `{fixture['id']}@{fixture['version']}` (`{fixture['license']}`) |",
        f"| Fixture SHA-256 | `{fixture['sha256']}` |",
        f"| Target SHA-256 | `{methodology['targets_sha256']}` |",
        f"| Exact command | `{_markdown_escape(methodology['exact_command'])}` |",
        "",
        "## Results",
        "",
        "`MEASURED_PARITY` means a committed open gate was met; it is not parity with an external vendor. `MEASURED_GAP` means the gate was missed. Browser evidence that was not collected remains `NOT_TESTED` with no substitute zero.",
        "",
        "| Dimension | Result label | Measurement | Predeclared gate |",
        "|---|---|---:|---|",
    ]
    for result in document["results"]:
        target = result["comparison_basis"]["target"]
        lines.append(
            "| "
            + " | ".join(
                (
                    _markdown_escape(result["dimension"]),
                    f"`{result['result_label']}`",
                    _markdown_escape(_markdown_value(result)),
                    _markdown_escape(_target_text(target)),
                )
            )
            + " |"
        )

    lines.extend(["", "## Raw evidence and limitations", ""])
    for result in document["results"]:
        lines.extend(
            [
                f"### {result['dimension']}",
                "",
                f"Result: `{result['result_label']}`. Measurement: {_markdown_value(result)}.",
                "",
                "Evidence: " + "; ".join(f"`{item}`" for item in result["evidence"]) + ".",
                "",
                "Raw measurements:",
                "",
                "```json",
                json.dumps(
                    result["raw_measurements"], indent=2, sort_keys=True, ensure_ascii=False
                ),
                "```",
                "",
                "Limitations:",
                "",
            ]
        )
        lines.extend(f"- {item}" for item in result["limitations"])
        lines.append("")

    lines.extend(["## Overall limitations", ""])
    lines.extend(f"- {item}" for item in document["overall_limitations"])
    lines.extend(
        [
            "",
            "The JSON artifact is canonical. This Markdown report and the CSV capability matrix were generated from the same in-memory document.",
            "",
        ]
    )
    return "\n".join(lines)


def _csv_measurement(result: Mapping[str, Any]) -> tuple[str, str]:
    measurement = result.get("measurement")
    if not isinstance(measurement, Mapping):
        return "", ""
    if measurement.get("value") is not None:
        return str(measurement["value"]), str(measurement.get("unit", ""))
    return json.dumps(measurement, sort_keys=True, separators=(",", ":")), "mixed"


def write_artifacts(document: Mapping[str, Any], output_dir: Path) -> dict[str, str]:
    validate_document(document)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "lyte-benchmark.json"
    markdown_path = output_dir / "lyte-benchmark.md"
    csv_path = output_dir / "lyte-capability-matrix.csv"
    json_path.write_text(_json_text(document), encoding="utf-8", newline="\n")
    markdown_path.write_text(render_markdown(document), encoding="utf-8", newline="\n")
    with csv_path.open("w", encoding="utf-8", newline="") as stream:
        fieldnames = [
            "category",
            "capability",
            "lyte_evidence",
            "comparison_subject",
            "comparison_basis",
            "source_reference",
            "result_label",
            "measured_value",
            "unit",
            "source_revision",
            "fixture_sha256",
            "limitations",
        ]
        writer = csv.DictWriter(stream, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for result in document["results"]:
            measured_value, unit = _csv_measurement(result)
            writer.writerow(
                {
                    "category": "business_observability_benchmark",
                    "capability": result["dimension"],
                    "lyte_evidence": "; ".join(result["evidence"]),
                    "comparison_subject": result["comparison_subject"],
                    "comparison_basis": "MEASURED"
                    if result["result_label"].startswith("MEASURED_")
                    else result["result_label"],
                    "source_reference": "benchmarks/observability/targets.json",
                    "result_label": result["result_label"],
                    "measured_value": measured_value,
                    "unit": unit,
                    "source_revision": document["subject"]["source_revision"],
                    "fixture_sha256": document["fixture"]["sha256"],
                    "limitations": " | ".join(result["limitations"]),
                }
            )
    return {
        "json": str(json_path),
        "markdown": str(markdown_path),
        "csv": str(csv_path),
    }


def run_benchmark(config: RunConfig) -> dict[str, Any]:
    fixture = _read_json(FIXTURE_PATH)
    targets = _read_json(TARGETS_PATH)
    browser_evidence: dict[str, Any] | None = None
    if config.browser_evidence is not None:
        browser_evidence = _read_browser_evidence(
            config.browser_evidence,
            config.source_revision,
        )
        browser_evidence["_artifact_path"] = str(config.browser_evidence)
        browser_evidence["_sha256"] = _file_sha256(config.browser_evidence)
    fixture_hash = _file_sha256(FIXTURE_PATH)
    targets_hash = _file_sha256(TARGETS_PATH)
    wall_started = time.perf_counter()
    cpu_started = time.process_time()
    peak_before, peak_method_before = _peak_rss_bytes()
    with TemporaryDirectory(prefix="lyte-open-benchmark-") as temporary:
        temporary_root = Path(temporary)
        workflow_samples = _measure_workflows(fixture, config, temporary_root)
        runtime = _measure_main_runtime(
            fixture,
            config,
            temporary_root / "runtime.sqlite3",
        )
        bundle = _measure_bundle()
    peak_after, peak_method_after = _peak_rss_bytes()
    resource_measurement = {
        "peak_rss_bytes": peak_after,
        "peak_rss_method": peak_method_after,
        "peak_rss_before_bytes": peak_before,
        "peak_rss_before_method": peak_method_before,
        "cpu_time_seconds": round(time.process_time() - cpu_started, 9),
        "wall_time_seconds": round(time.perf_counter() - wall_started, 9),
        "measurement_boundary": (
            "one runner process including fixture startup repetitions, query workload, "
            "OTLP ingest, SQLite persistence, and artifact preparation"
        ),
    }
    results = _build_results(
        workflow_samples,
        runtime,
        bundle,
        resource_measurement,
        targets,
        browser_evidence,
    )
    document = _build_document(
        config=config,
        fixture=fixture,
        fixture_hash=fixture_hash,
        targets=targets,
        targets_hash=targets_hash,
        results=results,
        browser_evidence=browser_evidence,
    )
    validate_document(document)
    write_artifacts(document, config.output_dir)
    return document


def _source_revision(value: str) -> str:
    normalized = value.strip().lower()
    if len(normalized) != 40 or any(character not in _SHA40 for character in normalized):
        raise argparse.ArgumentTypeError("must be an exact lowercase 40-character Git SHA")
    return normalized


def _positive(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def _non_negative(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be at least 0")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-revision", required=True, type=_source_revision)
    parser.add_argument(
        "--working-tree-state",
        required=True,
        choices=("clean", "modified", "unknown"),
    )
    parser.add_argument("--workflow-iterations", type=_positive, default=5)
    parser.add_argument("--query-iterations", type=_positive, default=30)
    parser.add_argument("--query-warmups", type=_non_negative, default=5)
    parser.add_argument("--ingest-batches", type=_positive, default=20)
    parser.add_argument("--spans-per-batch", type=_positive, default=10)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--browser-evidence",
        type=Path,
        help="real-browser evidence JSON generated by tools/capture_ui_evidence.py",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    namespace = _parser().parse_args(arguments)
    exact_command = shlex.join(
        [sys.executable, "-m", "benchmarks.observability.run_benchmark", *arguments]
    )
    config = RunConfig(
        source_revision=namespace.source_revision,
        working_tree_state=namespace.working_tree_state,
        workflow_iterations=namespace.workflow_iterations,
        query_iterations=namespace.query_iterations,
        query_warmups=namespace.query_warmups,
        ingest_batches=namespace.ingest_batches,
        spans_per_batch=namespace.spans_per_batch,
        output_dir=namespace.output_dir,
        exact_command=exact_command,
        browser_evidence=namespace.browser_evidence,
    )
    document = run_benchmark(config)
    counts = document["summary"]["result_label_counts"]
    print(
        json.dumps(
            {
                "run_id": document["run_id"],
                "result_label_counts": counts,
                "output_dir": str(config.output_dir),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
