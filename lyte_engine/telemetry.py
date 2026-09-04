"""OpenTelemetry-shaped and ATLAS business-event normalization."""
from __future__ import annotations

import time
from collections import Counter, defaultdict
from typing import Any, Mapping, Sequence

from .core import EVENT_CLASSES, SEVERITIES, clamp01, finite_float, percentile

def normalize_otel_spans(spans: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Normalize a bounded OpenTelemetry-shaped span batch.

    The endpoint accepts a simplified JSON projection rather than claiming to
    be a wire-compatible OTLP collector.
    """
    if not spans:
        raise ValueError("at least one span is required")
    if len(spans) > 5_000:
        raise ValueError("span batch exceeds 5,000 records")

    normalized: list[dict[str, Any]] = []
    by_span_id: dict[str, dict[str, Any]] = {}
    services: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    trace_ids: set[str] = set()

    for raw in spans:
        if not isinstance(raw, Mapping):
            raise ValueError("each span must be an object")
        trace_id = str(raw.get("trace_id", "")).strip()
        span_id = str(raw.get("span_id", "")).strip()
        service = " ".join(str(raw.get("service", "")).split())
        name = " ".join(str(raw.get("name", "")).split())
        if not trace_id or not span_id or not service or not name:
            raise ValueError("trace_id, span_id, service, and name are required")
        if span_id in by_span_id:
            raise ValueError("span_id values must be unique in a batch")
        duration = finite_float(
            raw.get("duration_ms", 0.0), name="duration_ms", minimum=0.0
        )
        status = str(raw.get("status", "UNSET")).strip().upper()
        if status not in {"OK", "ERROR", "UNSET"}:
            raise ValueError("span status must be OK, ERROR, or UNSET")
        attributes = raw.get("attributes") or {}
        if not isinstance(attributes, Mapping):
            raise ValueError("span attributes must be an object")
        safe_attributes = {
            str(key)[:80]: str(value)[:240]
            for key, value in list(attributes.items())[:32]
            if str(key).lower()
            not in {
                "prompt",
                "response",
                "request.body",
                "http.request.body",
                "db.statement",
                "authorization",
                "cookie",
            }
        }
        item = {
            "trace_id": trace_id[:64],
            "span_id": span_id[:32],
            "parent_span_id": str(raw.get("parent_span_id") or "")[:32] or None,
            "service": service[:100],
            "name": name[:160],
            "duration_ms": round(duration, 6),
            "status": status,
            "kind": str(raw.get("kind", "INTERNAL")).strip().upper()[:32],
            "attributes": safe_attributes,
        }
        normalized.append(item)
        by_span_id[item["span_id"]] = item
        services[item["service"]].append(item)
        trace_ids.add(item["trace_id"])

    edges: Counter[tuple[str, str]] = Counter()
    orphan_spans = 0
    for item in normalized:
        parent_id = item["parent_span_id"]
        if not parent_id:
            continue
        parent = by_span_id.get(parent_id)
        if parent is None:
            orphan_spans += 1
            continue
        if parent["service"] != item["service"]:
            edges[(parent["service"], item["service"])] += 1

    service_rows: list[dict[str, Any]] = []
    for service, rows in sorted(services.items()):
        durations = [float(row["duration_ms"]) for row in rows]
        errors = sum(row["status"] == "ERROR" for row in rows)
        service_rows.append(
            {
                "service": service,
                "span_count": len(rows),
                "error_count": errors,
                "error_rate": round(errors / len(rows), 8),
                "p50_duration_ms": round(percentile(durations, 0.50) or 0.0, 3),
                "p95_duration_ms": round(percentile(durations, 0.95) or 0.0, 3),
                "p99_duration_ms": round(percentile(durations, 0.99) or 0.0, 3),
                "truth_label": "MEASURED",
            }
        )
    continuity = 1.0 - orphan_spans / max(1, len(normalized))
    return {
        "schema": "szl.lyte-otel-normalized/v1",
        "mode": "OTEL_NORMALIZED_CALLER_INGEST",
        "wire_compatible_otlp_collector": False,
        "span_count": len(normalized),
        "trace_count": len(trace_ids),
        "service_count": len(service_rows),
        "orphan_span_count": orphan_spans,
        "trace_continuity": round(clamp01(continuity), 8),
        "services": service_rows,
        "service_graph": {
            "edges": [
                {
                    "from": source,
                    "to": target,
                    "span_count": count,
                    "relation": "OBSERVED_TRACE_EDGE",
                    "causality_claimed": False,
                }
                for (source, target), count in sorted(edges.items())
            ],
            "edge_count": len(edges),
            "causality_claimed": False,
        },
        "sensitive_attributes_removed": True,
        "prompt_content_recorded": False,
        "response_content_recorded": False,
        "effectors_enabled": False,
        "truth_label": "MEASURED",
    }


def summarize_atlas_events(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Summarize a bounded ATLAS-compatible business-event batch."""
    if not events:
        raise ValueError("at least one event is required")
    if len(events) > 2_000:
        raise ValueError("event batch exceeds 2,000 records")

    classes: Counter[str] = Counter()
    domains: Counter[str] = Counter()
    severities: Counter[str] = Counter()
    total_value = 0.0
    value_by_type: Counter[str] = Counter()
    slo_impacts: Counter[str] = Counter()
    timeline: list[dict[str, Any]] = []

    for raw in events:
        if not isinstance(raw, Mapping):
            raise ValueError("each event must be an object")
        event_id = str(raw.get("event_id", "")).strip()
        event_class = str(raw.get("event_class", "")).strip()
        domain = str(raw.get("domain", "")).strip()
        severity = str(raw.get("severity", "info")).strip().lower()
        if not event_id or not domain:
            raise ValueError("event_id and domain are required")
        if event_class not in EVENT_CLASSES:
            raise ValueError(f"unsupported event_class: {event_class}")
        if severity not in SEVERITIES:
            raise ValueError(f"unsupported severity: {severity}")
        timestamp = finite_float(
            raw.get("timestamp", time.time() * 1000),
            name="event.timestamp",
            minimum=0.0,
        )
        business_value = raw.get("business_value") or {}
        if not isinstance(business_value, Mapping):
            raise ValueError("business_value must be an object")
        amount = finite_float(
            business_value.get("amount", 0.0),
            name="business_value.amount",
            minimum=0.0,
        )
        value_type = str(business_value.get("type", "estimated")).strip().lower()
        if value_type not in {"at-risk", "protected", "created", "lost", "estimated"}:
            raise ValueError("unsupported business value type")
        slo = raw.get("slo_impact") or {}
        if not isinstance(slo, Mapping):
            raise ValueError("slo_impact must be an object")
        slo_impact = str(slo.get("impact", "none")).strip().lower()
        if slo_impact not in {"none", "at-risk", "breached", "recovered"}:
            raise ValueError("unsupported slo impact")

        classes[event_class] += 1
        domains[domain] += 1
        severities[severity] += 1
        total_value += amount
        value_by_type[value_type] += amount
        slo_impacts[slo_impact] += 1
        timeline.append(
            {
                "event_id": event_id[:128],
                "event_class": event_class,
                "domain": domain[:80],
                "severity": severity,
                "timestamp": timestamp,
                "business_value": {
                    "amount": round(amount, 2),
                    "currency": str(business_value.get("currency", "USD"))[:8],
                    "type": value_type,
                    "description": str(business_value.get("description", ""))[:240],
                },
                "slo_impact": {
                    "impact": slo_impact,
                    "slo_id": str(slo.get("slo_id", ""))[:128] or None,
                },
                "correlation_id": str(raw.get("correlation_id", ""))[:128] or None,
                "workflow_id": str(raw.get("workflow_id", ""))[:128] or None,
            }
        )

    timeline.sort(key=lambda item: item["timestamp"], reverse=True)
    critical = severities["critical"] + severities["high"]
    return {
        "schema": "szl.atlas-summary/v1",
        "event_count": len(events),
        "event_classes": dict(sorted(classes.items())),
        "domains": dict(sorted(domains.items())),
        "severities": dict(sorted(severities.items())),
        "slo_impacts": dict(sorted(slo_impacts.items())),
        "business_value": {
            "total_usd": round(total_value, 2),
            "by_type_usd": {
                key: round(value, 2) for key, value in sorted(value_by_type.items())
            },
        },
        "priority": {
            "high_or_critical_events": critical,
            "state": "CRITICAL" if critical else "WATCH" if severities["medium"] else "HEALTHY",
        },
        "timeline": timeline[:200],
        "truth_label": "REPORTED",
        "causality_claimed": False,
        "effectors_enabled": False,
    }


