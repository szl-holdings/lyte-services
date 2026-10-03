from __future__ import annotations

import json
from copy import deepcopy
from uuid import uuid4

import pytest

from lyte.connectors import OtlpJsonIngestor
from lyte.connectors.base import strip_sensitive_attributes
from lyte.domain import OperationalRecordDraft, ReceiptDraft, Scope, TruthLabel

SENSITIVE_KEYS = (
    "token",
    "access_token",
    "refresh_token",
    "session_token",
    "auth_token",
    "id_token",
    "bearer_token",
    "client_secret",
    "CLIENTSECRET",
    "http.request.header.PROXYAUTHORIZATION",
    "api_key",
    "private_key",
    "password",
    "credentials",
    "cookie",
    "set_cookie",
    "authorization",
    "ACCESS_TOKEN",
    "ReFrEsH-ToKeN",
    "  Session : Token  ",
    "auth/token",
    "accessToken",
    "APIKey",
    "PRIVATEKEY",
    "aUtHoRiZaTiOn",
    "ＡＣＣＥＳＳ＿ＴＯＫＥＮ",
    "http.request.header.authorization",
    "HTTP.REQUEST.HEADER.AUTHORIZATION",
    "http.request.header.AuThOrIzAtIoN",
    "http-request-header-x-api-key",
    "http.response.header.Set-Cookie",
    "metadata['refresh_token']",
    "metadata/session/token",
    "rpc.request.metadata.authToken",
    "providerCredentials",
    "httpRequestHeaderAuthorization",
)

BENIGN_ATTRIBUTES = {
    "http.route": "/checkout",
    "http.request.header.content-type": "application/json",
    "http.request.header.x-request-id": "request-123",
    "metadata.region": "us-east",
    "authorization_status": "allowed",
    "http.authorization.status": "allowed",
    "token_count": 12,
    "access_token_expires_at": "2026-09-08T00:00:00Z",
    "private_key_algorithm": "Ed25519",
    "tokenizer": "fixture",
    "secretary": "role",
    "password_policy": "configured",
    "cookie_count": 2,
    "api_key_sha256": "0" * 64,
}


@pytest.mark.parametrize("key", SENSITIVE_KEYS)
@pytest.mark.parametrize("contract", ("receipt", "body", "metadata"))
@pytest.mark.parametrize("nested", (False, True))
def test_persisted_contracts_reject_credential_families(
    key: str, contract: str, nested: bool
) -> None:
    payload = {key: "fixture-sensitive-value"}
    if nested:
        payload = {"events": [{"nested": (payload,)}]}
    with pytest.raises(ValueError, match="sensitive field is forbidden") as exc:
        if contract == "receipt":
            ReceiptDraft(
                kind="test.observed",
                subject_type="service",
                subject_id="checkout",
                payload=payload,
                truth_label=TruthLabel.SAMPLE,
            )
        else:
            OperationalRecordDraft(
                entity_kind="Service",
                entity_id="checkout",
                name="Checkout",
                body=payload if contract == "body" else {},
                metadata=payload if contract == "metadata" else {},
                truth_label=TruthLabel.SAMPLE,
            )
    if nested:
        assert "events[0].nested[0]" in str(exc.value)
    assert "fixture-sensitive-value" not in str(exc.value)


@pytest.mark.parametrize("key", SENSITIVE_KEYS)
def test_connector_removes_qualified_credentials_without_removing_benign_fields(key: str) -> None:
    source = {**BENIGN_ATTRIBUTES, key: "fixture-sensitive-value"}
    clean, stripped = strip_sensitive_attributes(source)
    assert clean == BENIGN_ATTRIBUTES
    assert stripped == (key,)
    assert source[key] == "fixture-sensitive-value"


def test_nested_connector_sanitization_preserves_containers_and_reports_paths() -> None:
    source = {
        "http": {"request": {"header": {"Authorization": "fixture-sensitive-value"}}},
        "metadata": [
            {"accessToken": "fixture-sensitive-value", "token_count": 4},
            ({"refresh-token": "fixture-sensitive-value", "region": "us-east"},),
        ],
        "attributes": BENIGN_ATTRIBUTES,
    }
    original = deepcopy(source)
    clean, stripped = strip_sensitive_attributes(source)
    assert clean == {
        "http": {"request": {"header": {}}},
        "metadata": [{"token_count": 4}, ({"region": "us-east"},)],
        "attributes": BENIGN_ATTRIBUTES,
    }
    assert stripped == (
        "http.request.header.Authorization",
        "metadata[0].accessToken",
        "metadata[1][0].refresh-token",
    )
    assert source == original
    assert "fixture-sensitive-value" not in repr((clean, stripped))
    draft = ReceiptDraft(
        kind="test.sanitized",
        subject_type="service",
        subject_id="checkout",
        payload=clean,
        truth_label=TruthLabel.SAMPLE,
    )
    assert draft.payload == clean


def test_otlp_sanitizes_credentials_at_resource_scope_and_record_boundaries() -> None:
    attributes = [
        {"key": key, "value": {"stringValue": "fixture-sensitive-value"}} for key in SENSITIVE_KEYS
    ] + [{"key": "http.route", "value": {"stringValue": "/checkout"}}]
    payload = {
        "resourceSpans": [
            {
                "resource": {"attributes": attributes},
                "scopeSpans": [
                    {
                        "scope": {"name": "fixture", "attributes": attributes},
                        "spans": [
                            {
                                "traceId": "1" * 32,
                                "spanId": "2" * 16,
                                "name": "checkout.request",
                                "startTimeUnixNano": "100",
                                "endTimeUnixNano": "250",
                                "attributes": attributes,
                            }
                        ],
                    }
                ],
            }
        ]
    }
    batch = OtlpJsonIngestor().ingest(
        json.dumps(payload).encode(),
        scope=Scope(uuid4(), uuid4()),
        idempotency_key="sanitize-otlp-0001",
    )
    assert batch.records[0].attributes == {"http.route": "/checkout"}
    assert batch.stripped_attribute_keys == tuple(sorted(SENSITIVE_KEYS))
    assert "fixture-sensitive-value" not in repr(batch)
