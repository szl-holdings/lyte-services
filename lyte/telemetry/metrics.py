"""Low-cardinality Prometheus metrics for Lyte itself."""

from __future__ import annotations

import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

_LABEL = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,63}$")
_OUTCOMES = frozenset({"success", "unavailable", "rejected", "error", "replay"})


def _label(value: str, *, name: str) -> str:
    normalized = str(value).strip()
    if not _LABEL.fullmatch(normalized):
        raise ValueError(f"{name} must be a bounded low-cardinality label")
    return normalized


def _outcome(value: str) -> str:
    normalized = str(value).strip().lower()
    if normalized not in _OUTCOMES:
        raise ValueError(f"outcome must be one of {', '.join(sorted(_OUTCOMES))}")
    return normalized


@dataclass(slots=True)
class OperationObservation:
    outcome: str = "success"

    def mark(self, outcome: str) -> None:
        self.outcome = _outcome(outcome)


class LyteMetrics:
    """An isolated metrics registry safe to instantiate in tests and app factories."""

    content_type = CONTENT_TYPE_LATEST

    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry = registry or CollectorRegistry(auto_describe=True)
        self.connector_requests = Counter(
            "lyte_connector_requests_total",
            "Bounded connector operations.",
            ("connector", "outcome"),
            registry=self.registry,
        )
        self.connector_duration = Histogram(
            "lyte_connector_duration_seconds",
            "Bounded connector operation duration.",
            ("connector",),
            buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
            registry=self.registry,
        )
        self.ingest_records = Counter(
            "lyte_ingest_records_total",
            "Telemetry or event records accepted or rejected.",
            ("signal", "outcome"),
            registry=self.registry,
        )
        self.query_requests = Counter(
            "lyte_query_requests_total",
            "Intelligence queries by bounded query name.",
            ("query", "outcome"),
            registry=self.registry,
        )
        self.query_duration = Histogram(
            "lyte_query_duration_seconds",
            "Intelligence query duration.",
            ("query",),
            buckets=(0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5),
            registry=self.registry,
        )
        self.receipts = Counter(
            "lyte_receipts_total",
            "Receipt drafts emitted by bounded kind.",
            ("kind",),
            registry=self.registry,
        )
        self.rejected_requests = Counter(
            "lyte_rejected_requests_total",
            "Requests rejected by a bounded reason category.",
            ("reason",),
            registry=self.registry,
        )
        self.db_pool_healthy = Gauge(
            "lyte_db_pool_healthy",
            "Whether the last database pool probe succeeded (1 or 0).",
            registry=self.registry,
        )
        self.build_info = Gauge(
            "lyte_build_info",
            "Static build identity.",
            ("version", "revision"),
            registry=self.registry,
        )

    def record_connector(self, connector: str, outcome: str, duration_seconds: float) -> None:
        connector_label = _label(connector, name="connector")
        outcome_label = _outcome(outcome)
        if duration_seconds < 0:
            raise ValueError("duration_seconds must be non-negative")
        self.connector_requests.labels(connector_label, outcome_label).inc()
        self.connector_duration.labels(connector_label).observe(duration_seconds)

    def record_ingest(self, signal: str, outcome: str, *, record_count: int = 1) -> None:
        signal_label = _label(signal, name="signal")
        outcome_label = _outcome(outcome)
        if isinstance(record_count, bool) or not isinstance(record_count, int) or record_count < 0:
            raise ValueError("record_count must be a non-negative integer")
        self.ingest_records.labels(signal_label, outcome_label).inc(record_count)

    def record_query(self, query: str, outcome: str, duration_seconds: float) -> None:
        query_label = _label(query, name="query")
        outcome_label = _outcome(outcome)
        if duration_seconds < 0:
            raise ValueError("duration_seconds must be non-negative")
        self.query_requests.labels(query_label, outcome_label).inc()
        self.query_duration.labels(query_label).observe(duration_seconds)

    def record_receipt(self, kind: str) -> None:
        self.receipts.labels(_label(kind, name="kind")).inc()

    def record_rejection(self, reason: str) -> None:
        self.rejected_requests.labels(_label(reason, name="reason")).inc()

    def set_db_pool_healthy(self, healthy: bool) -> None:
        if not isinstance(healthy, bool):
            raise TypeError("healthy must be a boolean")
        self.db_pool_healthy.set(1 if healthy else 0)

    def set_build_info(self, *, version: str, revision: str) -> None:
        self.build_info.labels(
            _label(version, name="version"),
            _label(revision, name="revision"),
        ).set(1)

    @contextmanager
    def track_connector(self, connector: str) -> Iterator[OperationObservation]:
        observation = OperationObservation()
        started = time.perf_counter()
        try:
            yield observation
        except Exception:
            observation.mark("error")
            raise
        finally:
            self.record_connector(connector, observation.outcome, time.perf_counter() - started)

    @contextmanager
    def track_query(self, query: str) -> Iterator[OperationObservation]:
        observation = OperationObservation()
        started = time.perf_counter()
        try:
            yield observation
        except Exception:
            observation.mark("error")
            raise
        finally:
            self.record_query(query, observation.outcome, time.perf_counter() - started)

    def render(self) -> bytes:
        return generate_latest(self.registry)


__all__ = ["LyteMetrics", "OperationObservation"]
