from __future__ import annotations

import json
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from lyte.app import create_app
from lyte.connectors import sign_governed_event

TENANT_ID = "11111111-1111-4111-8111-111111111111"
WORKSPACE_ID = "22222222-2222-4222-8222-222222222222"
DEV_TOKEN = "local-api-contract-token-" + "x" * 40
WEBHOOK_SECRET = "webhook-contract-secret-" + "s" * 40
IDENTITY_ROUTES = (
    "/healthz",
    "/readyz",
    "/api/build-info",
    "/api/source",
    "/.well-known/szl-source.json",
)
IDENTITY_FIELDS = (
    "source_repository",
    "source_revision",
    "runtime_repository",
    "runtime_source_revision",
    "effectors_enabled",
    "human_approval_required",
)


def _base_environment(monkeypatch: object, *, auth: bool = False) -> None:
    monkeypatch.setenv("LYTE_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    monkeypatch.setenv("LYTE_DEMO_MODE", "true")
    for name in (
        "LYTE_SOURCE_REVISION",
        "SOURCE_REVISION",
        "GITHUB_SHA",
        "LYTE_REQUIRE_SOURCE_BINDING",
    ):
        monkeypatch.delenv(name, raising=False)
    if auth:
        monkeypatch.setenv("LYTE_DEV_AUTH_ENABLED", "true")
        monkeypatch.setenv("LYTE_DEV_AUTH_TOKEN", DEV_TOKEN)
        monkeypatch.setenv("LYTE_DEV_TENANT_ID", TENANT_ID)
        monkeypatch.setenv("LYTE_DEV_WORKSPACE_ID", WORKSPACE_ID)
        monkeypatch.setenv("LYTE_WEBHOOK_HMAC_SECRET", WEBHOOK_SECRET)
    else:
        for name in (
            "LYTE_DEV_AUTH_ENABLED",
            "LYTE_DEV_AUTH_TOKEN",
            "LYTE_DEV_TENANT_ID",
            "LYTE_DEV_WORKSPACE_ID",
            "LYTE_WEBHOOK_HMAC_SECRET",
        ):
            monkeypatch.delenv(name, raising=False)


def _auth_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {DEV_TOKEN}",
        "X-Lyte-Tenant-ID": TENANT_ID,
        "X-Lyte-Workspace-ID": WORKSPACE_ID,
    }


def _identity_tuple(payload: dict[str, object]) -> tuple[object, ...]:
    return tuple(payload[field] for field in IDENTITY_FIELDS)


def _otlp_body() -> bytes:
    return json.dumps(
        {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [
                            {
                                "key": "service.name",
                                "value": {"stringValue": "orders-api"},
                            }
                        ]
                    },
                    "scopeSpans": [
                        {
                            "scope": {"name": "api-contract"},
                            "spans": [
                                {
                                    "traceId": "1" * 32,
                                    "spanId": "2" * 16,
                                    "name": "orders.request",
                                    "kind": "SPAN_KIND_SERVER",
                                    "startTimeUnixNano": "100",
                                    "endTimeUnixNano": "250",
                                    "attributes": [
                                        {
                                            "key": "http.route",
                                            "value": {"stringValue": "/orders"},
                                        },
                                        {
                                            "key": "authorization",
                                            "value": {"stringValue": "must-not-survive"},
                                        },
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ]
        },
        separators=(",", ":"),
    ).encode()


def test_public_sample_routes_are_complete_persisted_and_truth_labeled(monkeypatch) -> None:
    _base_environment(monkeypatch)
    with TestClient(create_app()) as client:
        required = (
            "/healthz",
            "/readyz",
            "/api/build-info",
            "/api/source",
            "/.well-known/szl-source.json",
            "/metrics",
            "/api/lyte/v2/catalog",
            "/api/lyte/v2/capabilities",
            "/api/lyte/v2/anatomy",
            "/api/lyte/v2/formulas",
            "/api/lyte/v2/sources",
            "/api/lyte/v2/services",
            "/api/lyte/v2/services/checkout-api",
            "/api/lyte/v2/journeys",
            "/api/lyte/v2/journeys/checkout",
            "/api/lyte/v2/outcomes",
            "/api/lyte/v2/agents",
            "/api/lyte/v2/incidents",
            "/api/lyte/v2/decisions",
            "/api/lyte/v2/playback",
            "/api/lyte/v2/second-brain",
            "/api/lyte/v2/evidence",
            "/api/lyte/v2/receipts",
        )
        for path in required:
            response = client.get(path)
            assert response.status_code == 200, (path, response.text)

        ready = client.get("/readyz").json()
        assert ready["ready"] is True
        assert ready["checks"]["database"] == "READY"
        assert ready["checks"]["source_binding"] == "UNBOUND_OPTIONAL_LOCAL"
        build = client.get("/api/build-info").json()
        assert build["persistence"]["state"] == "READY"
        assert build["data_mode"] == "SAMPLE"
        assert build["build"]["state"] == "UNBOUND"
        assert build["source_repository"] == "szl-holdings/lyte-services"
        assert build["runtime_repository"] == build["source_repository"]
        assert build["source_revision"] is None
        assert build["runtime_source_revision"] is None
        assert build["human_approval_required"] is True
        expected_identity = _identity_tuple(build)
        assert expected_identity == (
            "szl-holdings/lyte-services",
            None,
            "szl-holdings/lyte-services",
            None,
            False,
            True,
        )
        for path in IDENTITY_ROUTES:
            assert _identity_tuple(client.get(path).json()) == expected_identity

        services = client.get("/api/lyte/v2/services?limit=1&offset=0").json()
        assert services["count"] == 1
        assert services["next_offset"] == 1
        assert services["data_mode"] == "SAMPLE"
        detail = client.get("/api/lyte/v2/services/checkout-api").json()
        assert detail["body"]["service_id"] == "checkout-api"
        assert detail["truth_label"] == "SAMPLE"
        assert len(detail["record_hash"]) == 64

        memory = client.get("/api/lyte/v2/second-brain").json()
        assert memory["count"] == 1
        assert memory["raw_session_token_recorded"] is False
        assert memory["scope_digest_exposed"] is False
        assert memory["items"][0]["receipt_hash"]


def test_ask_and_hatun_are_deterministic_and_never_execute(monkeypatch) -> None:
    _base_environment(monkeypatch)
    with TestClient(create_app()) as client:
        answer = client.post(
            "/api/lyte/v2/ask",
            json={"question": "Why is checkout revenue at risk?"},
        )
        assert answer.status_code == 200
        body = answer.json()
        assert body["truth_label"] == "MODELED"
        assert "$924,000" in body["answer"]
        assert body["confidence_basis"] == "DETERMINISTIC_QUERY_MATCH"
        assert len(body["evidence_receipt_ids"][0]) == 64
        assert body["causality_claimed"] is False
        assert body["can_execute"] is False

        unknown = client.post(
            "/api/lyte/v2/ask",
            json={"question": "Predict a guaranteed acquisition target."},
        ).json()
        assert unknown["answer"] is None
        assert unknown["truth_label"] == "UNAVAILABLE"
        assert unknown["confidence"] == 0.0

        review = client.post(
            "/api/lyte/v2/hatun/evaluate",
            json={
                "action_type": "investigate-checkout",
                "evidence_labels": ["SAMPLE", "MODELED"],
                "evidence_receipt_ids": body["evidence_receipt_ids"],
                "formula_ids": body["formula_ids"],
            },
        ).json()
        assert review["decision"] == "REVIEW"
        assert review["truth_label"] == "SAMPLE"
        assert review["can_authorize"] is False
        assert review["can_execute"] is False
        assert review["effectors_enabled"] is False

        denied = client.post(
            "/api/lyte/v2/hatun/evaluate",
            json={
                "action_type": "rollback",
                "evidence_labels": ["MEASURED"],
                "requests_execution": True,
            },
        ).json()
        assert denied["decision"] == "DENY"
        assert denied["can_execute"] is False


def test_source_identity_mismatch_fails_required_readiness(monkeypatch) -> None:
    _base_environment(monkeypatch)
    monkeypatch.setenv("LYTE_REQUIRE_SOURCE_BINDING", "true")
    monkeypatch.setenv("LYTE_SOURCE_REVISION", "a" * 40)
    monkeypatch.setenv("SOURCE_REVISION", "b" * 40)
    with TestClient(create_app()) as client:
        ready = client.get("/readyz")
        assert ready.status_code == 503
        assert ready.json()["checks"]["source_binding"] == "MISMATCH"
        build = client.get("/api/build-info").json()
        assert build["build"]["state"] == "MISMATCH"
        assert build["build"]["revision"] is None
        assert build["source_binding"]["bindings_agree"] is False
        assert build["source_binding"]["distinct_revision_count"] == 2
        expected_identity = _identity_tuple(build)
        assert expected_identity == (
            "szl-holdings/lyte-services",
            None,
            "szl-holdings/lyte-services",
            None,
            False,
            True,
        )
        for path in IDENTITY_ROUTES:
            response = client.get(path)
            assert response.status_code == (503 if path == "/readyz" else 200)
            assert _identity_tuple(response.json()) == expected_identity


def test_source_identity_tuple_is_exact_on_every_operational_surface(monkeypatch) -> None:
    _base_environment(monkeypatch)
    revision = "c" * 40
    monkeypatch.setenv("LYTE_REQUIRE_SOURCE_BINDING", "true")
    monkeypatch.setenv("LYTE_SOURCE_REVISION", revision)
    with TestClient(create_app()) as client:
        expected_identity = (
            "szl-holdings/lyte-services",
            revision,
            "szl-holdings/lyte-services",
            revision,
            False,
            True,
        )
        for path in IDENTITY_ROUTES:
            response = client.get(path)
            assert response.status_code == 200, (path, response.text)
            assert _identity_tuple(response.json()) == expected_identity


def test_security_headers_support_hf_embed_without_external_assets(monkeypatch) -> None:
    _base_environment(monkeypatch)
    with TestClient(create_app()) as client:
        root = client.get("/")
        policy = root.headers["content-security-policy"]
        assert policy.startswith("default-src 'self'")
        assert "https://huggingface.co" in policy
        assert "unsafe-inline" not in policy
        assert root.headers["cross-origin-resource-policy"] == "same-origin"
        api = client.get("/api/build-info")
        assert api.headers["cache-control"] == "no-store"


def test_anonymous_mutations_fail_closed(monkeypatch) -> None:
    _base_environment(monkeypatch)
    with TestClient(create_app()) as client:
        for path in (
            "/api/lyte/v2/ingest/otlp",
            "/api/lyte/v2/ingest/event",
            "/api/lyte/v2/ingest/github/szl-holdings/lyte-services",
            "/api/lyte/v2/analyze",
        ):
            response = client.post(path, content=b"{}")
            assert response.status_code == 503
            assert "request denied" in response.json()["detail"]


def test_identity_is_checked_before_mutation_scope(monkeypatch) -> None:
    _base_environment(monkeypatch, auth=True)
    with TestClient(create_app()) as client:
        response = client.post("/api/lyte/v2/ingest/otlp", content=b"{}")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["detail"] == "bearer authentication failed"


def test_public_github_observation_never_mutates_sample_state(monkeypatch) -> None:
    from lyte.api import routes_ingest

    class _Observation:
        def to_dict(self) -> dict[str, object]:
            return {
                "repository": "szl-holdings/lyte-services",
                "state": "READY",
                "truth_label": "REPORTED",
                "workflow_runs": [],
            }

    _base_environment(monkeypatch)
    monkeypatch.setattr(routes_ingest, "_fetch_github", lambda *args, **kwargs: _Observation())
    with TestClient(create_app()) as client:
        before = client.get("/api/lyte/v2/receipts").json()["count"]
        response = client.get("/api/lyte/v2/github/szl-holdings/lyte-services")
        after = client.get("/api/lyte/v2/receipts").json()["count"]
        assert response.status_code == 200
        assert response.json()["receipt_persisted"] is False
        assert response.json()["persistence_requires_authenticated_ingest"] is True
        assert before == after


def test_authenticated_otlp_analysis_and_event_are_durable_and_idempotent(
    monkeypatch,
) -> None:
    _base_environment(monkeypatch, auth=True)
    headers = {**_auth_headers(), "Idempotency-Key": "otlp-api-contract-0001"}
    with TestClient(create_app()) as client:
        first = client.post(
            "/api/lyte/v2/ingest/otlp",
            content=_otlp_body(),
            headers={**headers, "Content-Type": "application/json"},
        )
        assert first.status_code == 200, first.text
        first_body = first.json()
        assert first_body["record_count"] == 1
        assert first_body["records"][0]["attributes"] == {"http.route": "/orders"}
        assert "must-not-survive" not in first.text
        assert first_body["durable"] is True
        assert len(first_body["receipt_id"]) == 64

        replay = client.post(
            "/api/lyte/v2/ingest/otlp",
            content=_otlp_body(),
            headers={**headers, "Content-Type": "application/json"},
        )
        assert replay.status_code == 200
        assert replay.json()["receipt_id"] == first_body["receipt_id"]
        assert replay.json()["projection_id"] == first_body["projection_id"]

        analysis = client.post(
            "/api/lyte/v2/analyze",
            headers={**_auth_headers(), "Idempotency-Key": "analysis-api-contract-0001"},
            json={
                "service_id": "orders-api",
                "good_events": 99_500,
                "total_events": 100_000,
                "slo_target": 0.999,
                "requests": 100_000,
                "window_seconds": 300.0,
                "failed_changes": 1,
                "total_changes": 20,
                "cost_usd": 200.0,
                "successful_outcomes": 99_500,
                "revenue_volume": 100_000,
                "baseline_conversion_rate": 0.72,
                "observed_conversion_rate": 0.70,
                "average_order_value": 84.0,
                "currency": "USD",
                "evidence_refs": [first_body["receipt_id"]],
                "input_truth_label": "REPORTED",
            },
        )
        assert analysis.status_code == 200, analysis.text
        analysis_body = analysis.json()
        assert len(analysis_body["receipt_id"]) == 64
        assert len(analysis_body["anatomy_trace"]) == 9
        assert all(stage["state"] == "COMPLETE" for stage in analysis_body["anatomy_trace"])
        assert analysis_body["causality_claimed"] is False
        assert analysis_body["can_authorize"] is False

        timestamp = str(int(datetime.now(UTC).timestamp()))
        nonce = "event-nonce-00000001"
        event_body = json.dumps(
            {
                "schema_version": "lyte.event.v1",
                "event_id": "orders-event-0001",
                "source_id": "lyte-test-source",
                "event_type": "orders.degraded",
                "subject_type": "service",
                "subject_id": "orders-api",
                "occurred_at": "2026-09-04T12:00:00Z",
                "truth_label": "REPORTED",
                "attributes": {"error_count": 500},
                "evidence_refs": [first_body["receipt_id"]],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        signature = sign_governed_event(
            WEBHOOK_SECRET.encode(),
            timestamp=timestamp,
            nonce=nonce,
            body=event_body,
        )
        event_headers = {
            **_auth_headers(),
            "Idempotency-Key": "event-api-contract-0001",
            "X-Lyte-Signature": signature,
            "X-Lyte-Timestamp": timestamp,
            "X-Lyte-Nonce": nonce,
            "Content-Type": "application/json",
        }
        accepted = client.post(
            "/api/lyte/v2/ingest/event",
            content=event_body,
            headers=event_headers,
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["signature_verified"] is True
        assert accepted.json()["durable"] is True
        replay_event = client.post(
            "/api/lyte/v2/ingest/event",
            content=event_body,
            headers=event_headers,
        )
        assert replay_event.status_code == 409


def test_body_and_pagination_limits_are_enforced(monkeypatch) -> None:
    _base_environment(monkeypatch)
    with TestClient(create_app()) as client:
        assert client.get("/api/lyte/v2/services?limit=101").status_code == 422
        assert client.get("/api/lyte/v2/services?offset=-1").status_code == 422
        oversized = client.post(
            "/api/lyte/v2/ask",
            content=b"x" * 2_000_001,
            headers={"Content-Type": "application/json"},
        )
        assert oversized.status_code == 413
        chunked = client.post(
            "/api/lyte/v2/ask",
            content=iter((b"x" * 1_000_001, b"x" * 1_000_001)),
            headers={"Content-Type": "application/json"},
        )
        assert chunked.status_code == 413
