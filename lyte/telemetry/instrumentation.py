"""OpenTelemetry/FastAPI setup without exporting secrets or arbitrary URLs."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI
from fastapi.responses import Response
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import Span, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter

from lyte.connectors.base import is_sensitive_attribute_key

from .metrics import LyteMetrics

_IDENTIFIER = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,127}$")


def _identifier(value: str, *, name: str) -> str:
    normalized = str(value).strip()
    if not _IDENTIFIER.fullmatch(normalized):
        raise ValueError(f"{name} must be a safe bounded identifier")
    return normalized


def safe_telemetry_attributes(attributes: Mapping[str, Any]) -> dict[str, Any]:
    """Remove secret-bearing keys and explicitly summarize oversized strings."""

    if len(attributes) > 64:
        raise ValueError("telemetry attributes exceed the limit of 64")
    safe: dict[str, Any] = {}
    for raw_key, raw_value in attributes.items():
        key = _identifier(str(raw_key), name="telemetry attribute key")
        if is_sensitive_attribute_key(key):
            continue
        if isinstance(raw_value, bool | int | float):
            safe[key] = raw_value
        elif isinstance(raw_value, str):
            if len(raw_value) <= 512:
                safe[key] = raw_value
            else:
                digest = hashlib.sha256(raw_value.encode("utf-8")).hexdigest()[:16]
                safe[key] = f"[OVERSIZE_REDACTED sha256_prefix={digest}]"
        elif raw_value is None:
            continue
        else:
            raise TypeError(f"telemetry attribute {key} must be a scalar")
    return safe


def structured_log_event(event: str, attributes: Mapping[str, Any]) -> str:
    """Produce compact deterministic JSON after telemetry-safe redaction."""

    return json.dumps(
        {"event": _identifier(event, name="event"), **safe_telemetry_attributes(attributes)},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


@dataclass(frozen=True, slots=True)
class TelemetryConfig:
    service_name: str = "szl-lyte-enterprise"
    service_version: str = "4.0.0"
    deployment_environment: str = "development"
    source_revision: str = "unknown"

    def __post_init__(self) -> None:
        for field in (
            "service_name",
            "service_version",
            "deployment_environment",
            "source_revision",
        ):
            _identifier(getattr(self, field), name=field)


@dataclass(frozen=True, slots=True)
class TelemetryRuntime:
    metrics: LyteMetrics
    tracer_provider: TracerProvider


def build_tracer_provider(
    config: TelemetryConfig,
    *,
    exporter: SpanExporter | None = None,
) -> TracerProvider:
    """Build a local provider; an exporter must be supplied explicitly."""

    resource = Resource.create(
        {
            "service.name": config.service_name,
            "service.version": config.service_version,
            "deployment.environment.name": config.deployment_environment,
            "service.source.revision": config.source_revision,
        }
    )
    provider = TracerProvider(resource=resource)
    if exporter is not None:
        provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider


def instrument_fastapi(
    application: FastAPI,
    *,
    tracer_provider: TracerProvider,
    excluded_urls: str = "/healthz,/readyz,/metrics",
) -> None:
    """Instrument one FastAPI application without capturing request headers."""

    if getattr(application, "_is_instrumented_by_opentelemetry", False):
        return

    def sanitize_server_request(span: Span, scope: Mapping[str, Any]) -> None:
        if not span or not span.is_recording():
            return
        scheme = str(scope.get("scheme", "http"))
        path = str(scope.get("path", "/"))
        safe_url = f"{scheme}://lyte.invalid{path}"
        # ASGI instrumentation records the original query in http.url by
        # default. Overwrite both semantic-convention generations explicitly.
        span.set_attribute("http.url", safe_url)
        span.set_attribute("url.full", safe_url)
        span.set_attribute("http.target", path)
        span.set_attribute("url.query", "[REDACTED]" if scope.get("query_string") else "")
        span.set_attribute("http.user_agent", "[REDACTED]")
        span.set_attribute("user_agent.original", "[REDACTED]")

    FastAPIInstrumentor.instrument_app(
        application,
        tracer_provider=tracer_provider,
        excluded_urls=excluded_urls,
        server_request_hook=sanitize_server_request,
        http_capture_headers_server_request=[],
        http_capture_headers_server_response=[],
        http_capture_headers_sanitize_fields=[".*"],
        exclude_spans=["receive", "send"],
    )


def setup_telemetry(
    application: FastAPI,
    *,
    config: TelemetryConfig | None = None,
    metrics: LyteMetrics | None = None,
    tracer_provider: TracerProvider | None = None,
    exporter: SpanExporter | None = None,
    expose_metrics: bool = True,
) -> TelemetryRuntime:
    """Wire OpenTelemetry and an optional Prometheus-compatible endpoint."""

    actual_config = config or TelemetryConfig()
    actual_metrics = metrics or LyteMetrics()
    provider = tracer_provider or build_tracer_provider(actual_config, exporter=exporter)
    instrument_fastapi(application, tracer_provider=provider)
    actual_metrics.set_build_info(
        version=actual_config.service_version,
        revision=actual_config.source_revision,
    )
    if expose_metrics and not any(
        getattr(route, "path", None) == "/metrics" for route in application.routes
    ):

        def metrics_endpoint() -> Response:
            return Response(actual_metrics.render(), media_type=actual_metrics.content_type)

        application.add_api_route(
            "/metrics",
            metrics_endpoint,
            methods=["GET"],
            include_in_schema=False,
            name="prometheus_metrics",
        )
    application.state.lyte_metrics = actual_metrics
    application.state.lyte_tracer_provider = provider
    return TelemetryRuntime(metrics=actual_metrics, tracer_provider=provider)


@contextmanager
def operation_span(
    tracer_provider: TracerProvider,
    name: str,
    *,
    attributes: Mapping[str, Any] | None = None,
) -> Iterator[Span]:
    """Create one explicitly named internal span with sanitized attributes."""

    span_name = _identifier(name, name="span name")
    tracer = trace.get_tracer(
        "lyte.telemetry",
        tracer_provider=tracer_provider,
    )
    with tracer.start_as_current_span(
        span_name,
        attributes=safe_telemetry_attributes(attributes or {}),
    ) as span:
        yield span


configure_telemetry = setup_telemetry

__all__ = [
    "TelemetryConfig",
    "TelemetryRuntime",
    "build_tracer_provider",
    "configure_telemetry",
    "instrument_fastapi",
    "operation_span",
    "safe_telemetry_attributes",
    "setup_telemetry",
    "structured_log_event",
]
