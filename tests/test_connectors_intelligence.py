from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from lyte.connectors import (
    ConnectorPolicyError,
    ConnectorState,
    GitHubActionsConnector,
    GitHubActionsLimits,
    GovernedEventVerifier,
    OtlpJsonIngestor,
    OtlpLimits,
    PayloadValidationError,
    ReplayDetectedError,
    ReplayProtector,
    WebhookIdempotencyConflict,
    sign_governed_event,
)
from lyte.domain import Scope, TruthLabel
from lyte.intelligence import AskLyteEngine, build_checkout_scenario
from lyte.telemetry import (
    LyteMetrics,
    TelemetryConfig,
    build_tracer_provider,
    safe_telemetry_attributes,
    setup_telemetry,
    structured_log_event,
)


def _github_run() -> dict[str, object]:
    return {
        "id": 1842,
        "name": "CI",
        "status": "completed",
        "conclusion": "success",
        "head_branch": "main",
        "event": "push",
        "head_sha": "a" * 40,
        "created_at": "2026-09-04T12:00:00Z",
        "updated_at": "2026-09-04T12:01:30Z",
    }


def test_github_actions_is_allowlisted_conditional_bounded_and_read_only() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.host == "api.github.com"
        assert request.url.path == "/repos/szl-holdings/lyte/actions/runs"
        if request.headers.get("if-none-match") == '"fixture-etag"':
            return httpx.Response(304, headers={"etag": '"fixture-etag"'})
        return httpx.Response(
            200,
            json={"total_count": 1, "workflow_runs": [_github_run()]},
            headers={"etag": '"fixture-etag"'},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    connector = GitHubActionsConnector({"szl-holdings/lyte"}, client=client)
    result = connector.fetch_workflow_runs("SZL-HOLDINGS/LYTE")
    assert result.state is ConnectorState.OBSERVED
    assert result.complete is True
    assert result.runs[0].duration_ms == 90_000
    assert result.runs[0].head_sha == "a" * 40
    assert result.receipt.payload["read_only"] is True
    assert result.receipt.truth_label is TruthLabel.REPORTED

    unchanged = connector.fetch_workflow_runs("szl-holdings/lyte", etag='"fixture-etag"')
    assert unchanged.state is ConnectorState.NOT_MODIFIED
    assert unchanged.runs == ()
    assert requests[-1].headers["if-none-match"] == '"fixture-etag"'
    with pytest.raises(ConnectorPolicyError, match="allowlist"):
        connector.fetch_workflow_runs("attacker/repository")
    client.close()


@pytest.mark.parametrize("status", [302, 403, 429, 500])
def test_github_actions_remote_failures_are_unavailable_without_sample_fallback(
    status: int,
) -> None:
    response_headers = {"location": "https://example.invalid/escape"} if status == 302 else {}
    client = httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(status, headers=response_headers))
    )
    result = GitHubActionsConnector({"szl-holdings/lyte"}, client=client).fetch_workflow_runs(
        "szl-holdings/lyte"
    )
    assert result.state is ConnectorState.UNAVAILABLE
    assert result.runs == ()
    assert result.truth_label is TruthLabel.UNAVAILABLE
    assert result.receipt.truth_label is TruthLabel.UNAVAILABLE
    client.close()


def test_github_actions_rejects_body_overflow_and_marks_page_bound_partial() -> None:
    overflow_client = httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"x" * 1_025))
    )
    connector = GitHubActionsConnector(
        {"szl-holdings/lyte"},
        client=overflow_client,
        limits=GitHubActionsLimits(max_body_bytes=1_024),
    )
    assert connector.fetch_workflow_runs("szl-holdings/lyte").reason == "response_body_overflow"
    overflow_client.close()

    page_client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={"total_count": 2, "workflow_runs": [_github_run()]},
            )
        )
    )
    partial = GitHubActionsConnector(
        {"szl-holdings/lyte"},
        client=page_client,
        limits=GitHubActionsLimits(max_pages=1, per_page=1),
    ).fetch_workflow_runs("szl-holdings/lyte")
    assert partial.state is ConnectorState.OBSERVED_PARTIAL
    assert partial.complete is False
    assert len(partial.runs) == 1
    page_client.close()


@pytest.fixture
def scope() -> Scope:
    return Scope(uuid4(), uuid4())


def _otlp_trace_payload(*, spans: int = 1, unsupported: bool = False) -> bytes:
    span = {
        "traceId": "1" * 32,
        "spanId": "2" * 16,
        "name": "checkout.request",
        "kind": "SPAN_KIND_SERVER",
        "startTimeUnixNano": "100",
        "endTimeUnixNano": "250",
        "attributes": [
            {"key": "http.route", "value": {"stringValue": "/checkout"}},
            {"key": "authorization", "value": {"stringValue": "must-not-survive"}},
        ],
        "status": {"code": "STATUS_CODE_ERROR", "message": "fixture"},
    }
    if unsupported:
        span["events"] = []
    body = {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        {
                            "key": "service.name",
                            "value": {"stringValue": "checkout-api"},
                        }
                    ]
                },
                "scopeSpans": [
                    {
                        "scope": {"name": "fixture"},
                        "spans": [dict(span, spanId=f"{index + 2:016x}") for index in range(spans)],
                    }
                ],
            }
        ]
    }
    return json.dumps(body, separators=(",", ":")).encode()


def test_otlp_trace_subset_strips_sensitive_attributes_and_binds_scope(scope: Scope) -> None:
    batch = OtlpJsonIngestor().ingest(
        _otlp_trace_payload(),
        scope=scope,
        idempotency_key="otlp-request-0001",
    )
    assert batch.signal == "traces"
    assert batch.record_count == 1
    assert batch.records[0].attributes == {"http.route": "/checkout"}
    assert batch.stripped_attribute_keys == ("authorization",)
    assert "must-not-survive" not in repr(batch)
    assert batch.receipt.payload["tenant_id"] == str(scope.tenant_id)
    assert batch.receipt.truth_label is TruthLabel.REPORTED


def test_otlp_metric_and_log_subsets_are_real_protocol_shapes(scope: Scope) -> None:
    metric_body = json.dumps(
        {
            "resourceMetrics": [
                {
                    "scopeMetrics": [
                        {
                            "metrics": [
                                {
                                    "name": "checkout.duration",
                                    "unit": "ms",
                                    "gauge": {
                                        "dataPoints": [{"timeUnixNano": "500", "asDouble": 12.5}]
                                    },
                                },
                                {
                                    "name": "checkout.histogram",
                                    "histogram": {
                                        "aggregationTemporality": ("AGGREGATION_TEMPORALITY_DELTA"),
                                        "dataPoints": [
                                            {
                                                "timeUnixNano": "500",
                                                "count": "3",
                                                "sum": 9.0,
                                                "bucketCounts": ["1", "2"],
                                                "explicitBounds": [5.0],
                                            }
                                        ],
                                    },
                                },
                            ]
                        }
                    ]
                }
            ]
        },
        separators=(",", ":"),
    ).encode()
    metrics = OtlpJsonIngestor().ingest(
        metric_body, scope=scope, idempotency_key="otlp-metrics-0001"
    )
    assert metrics.signal == "metrics"
    assert [record.data["metric_type"] for record in metrics.records] == [
        "gauge",
        "histogram",
    ]

    log_body = json.dumps(
        {
            "resourceLogs": [
                {
                    "scopeLogs": [
                        {
                            "logRecords": [
                                {
                                    "timeUnixNano": "700",
                                    "severityNumber": 17,
                                    "severityText": "ERROR",
                                    "body": {"stringValue": "checkout failed"},
                                    "traceId": "1" * 32,
                                    "spanId": "2" * 16,
                                }
                            ]
                        }
                    ]
                }
            ]
        },
        separators=(",", ":"),
    ).encode()
    logs = OtlpJsonIngestor().ingest(log_body, scope=scope, idempotency_key="otlp-logs-000001")
    assert logs.signal == "logs"
    assert logs.records[0].data["body"] == "checkout failed"


def test_otlp_rejects_unsupported_malformed_nonfinite_and_over_limit(scope: Scope) -> None:
    with pytest.raises(PayloadValidationError, match="unsupported field.*events"):
        OtlpJsonIngestor().ingest(
            _otlp_trace_payload(unsupported=True),
            scope=scope,
            idempotency_key="otlp-request-0002",
        )
    with pytest.raises(PayloadValidationError, match="non-finite"):
        OtlpJsonIngestor().ingest(
            b'{"resourceMetrics":NaN}',
            scope=scope,
            idempotency_key="otlp-request-0003",
        )
    with pytest.raises(PayloadValidationError, match="limit of 1"):
        OtlpJsonIngestor(limits=OtlpLimits(max_records=1)).ingest(
            _otlp_trace_payload(spans=2),
            scope=scope,
            idempotency_key="otlp-request-0004",
        )
    duplicate = b'{"resourceSpans":[],"resourceSpans":[]}'
    with pytest.raises(PayloadValidationError, match="duplicate key"):
        OtlpJsonIngestor().ingest(
            duplicate,
            scope=scope,
            idempotency_key="otlp-request-0005",
        )


def _event_body(event_id: str = "event-0001") -> bytes:
    return json.dumps(
        {
            "schema_version": "lyte.event.v1",
            "event_id": event_id,
            "source_id": "billing-events",
            "event_type": "checkout.failed",
            "subject_type": "journey",
            "subject_id": "checkout",
            "occurred_at": "2026-09-04T12:00:00Z",
            "truth_label": "REPORTED",
            "attributes": {"segment": "web", "failures": 12},
            "evidence_refs": ["billing:batch:42"],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def test_governed_event_hmac_skew_nonce_and_idempotency(scope: Scope) -> None:
    secret = b"s" * 32
    now = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
    timestamp = str(int(now.timestamp()))
    body = _event_body()
    verifier = GovernedEventVerifier(
        allowed_sources={"billing-events"},
        secret=secret,
        replay_protector=ReplayProtector(max_entries=100),
    )

    def signature(nonce: str, payload: bytes = body) -> str:
        return sign_governed_event(
            secret,
            timestamp=timestamp,
            nonce=nonce,
            body=payload,
        )

    first_nonce = "nonce-0000000001"
    verified = verifier.verify(
        body,
        scope=scope,
        signature=signature(first_nonce),
        timestamp=timestamp,
        nonce=first_nonce,
        idempotency_key="event-request-0001",
        now=now,
    )
    assert verified.signature_verified is True
    assert verified.replayed is False
    assert verified.receipt.truth_label is TruthLabel.REPORTED

    with pytest.raises(ReplayDetectedError):
        verifier.verify(
            body,
            scope=scope,
            signature=signature(first_nonce),
            timestamp=timestamp,
            nonce=first_nonce,
            idempotency_key="event-request-0001",
            now=now,
        )
    retry_nonce = "nonce-0000000002"
    retry = verifier.verify(
        body,
        scope=scope,
        signature=signature(retry_nonce),
        timestamp=timestamp,
        nonce=retry_nonce,
        idempotency_key="event-request-0001",
        now=now,
    )
    assert retry.replayed is True

    changed = _event_body("event-0002")
    conflict_nonce = "nonce-0000000003"
    with pytest.raises(WebhookIdempotencyConflict):
        verifier.verify(
            changed,
            scope=scope,
            signature=signature(conflict_nonce, changed),
            timestamp=timestamp,
            nonce=conflict_nonce,
            idempotency_key="event-request-0001",
            now=now,
        )


def test_checkout_scenario_and_ask_are_deterministic_citation_first() -> None:
    first = build_checkout_scenario()
    second = build_checkout_scenario()
    assert first.scenario_sha256 == second.scenario_sha256
    assert first.to_dict() == second.to_dict()
    assert first.outcomes[0].indicators["revenue_at_risk_usd"].value == 924_000.0
    assert first.outcomes[0].indicators["revenue_at_risk_usd"].label is TruthLabel.MODELED
    assert first.action_requests[0].can_execute is False
    assert all(
        not factor.causality_claimed
        for incident in first.incidents
        for factor in incident.candidate_factors
    )

    engine = AskLyteEngine(first)
    answer = engine.answer("Why is checkout revenue at risk?").to_dict()
    assert next(iter(answer)) == "citations"
    assert answer["truth_label"] == "MODELED"
    assert answer["citations"]
    assert answer["can_execute"] is False
    unavailable = engine.answer("What improved after the approved action?")
    assert unavailable.truth_label is TruthLabel.UNAVAILABLE
    assert unavailable.answer is None
    assert "a witnessed execution receipt" in unavailable.missing_evidence


def test_prometheus_and_opentelemetry_instrumentation_are_bounded_and_redacted() -> None:
    metrics = LyteMetrics()
    metrics.record_connector("github_actions", "success", 0.25)
    metrics.record_ingest("traces", "rejected", record_count=2)
    metrics.record_query("ask_lyte", "success", 0.01)
    metrics.record_receipt("ingest.otlp.accepted")
    metrics.record_rejection("body_overflow")
    metrics.set_db_pool_healthy(True)
    rendered = metrics.render().decode()
    assert (
        'lyte_connector_requests_total{connector="github_actions",outcome="success"} 1.0'
        in rendered
    )
    assert "lyte_ingest_records_total" in rendered
    assert "lyte_db_pool_healthy 1.0" in rendered

    safe = safe_telemetry_attributes(
        {"authorization": "Bearer must-not-survive", "route": "/checkout"}
    )
    assert safe == {"route": "/checkout"}
    line = structured_log_event("request.completed", {"token": "must-not-survive", "status": 200})
    assert "must-not-survive" not in line

    app = FastAPI()

    @app.get("/probe")
    def probe() -> dict[str, bool]:
        return {"ok": True}

    exporter = InMemorySpanExporter()
    provider = build_tracer_provider(TelemetryConfig(source_revision="abc1234"), exporter=exporter)
    setup_telemetry(
        app,
        config=TelemetryConfig(source_revision="abc1234"),
        metrics=metrics,
        tracer_provider=provider,
    )
    with TestClient(app) as client:
        probe_response = client.get(
            "/probe?session_token=must-not-survive",
            headers={"Authorization": "Bearer must-not-survive"},
        )
        response = client.get("/metrics")
    assert probe_response.status_code == 200
    assert response.status_code == 200
    assert "lyte_build_info" in response.text
    exported = repr([dict(span.attributes) for span in exporter.get_finished_spans()])
    assert exporter.get_finished_spans()
    assert "must-not-survive" not in exported
