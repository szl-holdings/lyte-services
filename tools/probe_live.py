#!/usr/bin/env python3
"""Live route closure probe for an exact merged Lyte revision."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlsplit

READ_ROUTES = (
    "/healthz",
    "/readyz",
    "/api/live",
    "/build-receipt.json",
    "/api/build-info",
    "/api/source",
    "/.well-known/szl-source.json",
    "/api/lyte/v2/catalog",
    "/api/lyte/v2/capabilities",
    "/api/lyte/v2/anatomy",
    "/api/lyte/v2/formulas",
    "/api/lyte/v2/sources",
    "/api/lyte/v2/services",
    "/api/lyte/v2/journeys",
    "/api/lyte/v2/outcomes",
    "/api/lyte/v2/incidents",
    "/api/lyte/v2/decisions",
    "/api/lyte/v2/playback",
    "/api/lyte/v2/second-brain",
)

DENIED_MUTATION_ROUTES = (
    "/api/lyte/v2/admin/scopes",
    "/api/lyte/v2/ingest/otlp",
    "/api/lyte/v2/ingest/event",
    "/api/lyte/v2/ingest/github/szl-holdings/lyte-services",
    "/api/lyte/v2/analyze",
    "/api/lyte/v2/second-brain/observations",
    "/api/lyte/v2/second-brain/approved-knowledge",
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        del req, fp, code, msg, headers, newurl
        return None


_OPENER = urllib.request.build_opener(_RejectRedirects)


def validate_base_url(value: str) -> str:
    base = value.rstrip("/")
    try:
        parts = urlsplit(base)
        port = parts.port
    except ValueError as exc:
        raise ValueError("base URL contains an invalid port") from exc
    loopback = parts.hostname in {"127.0.0.1", "localhost", "::1"}
    if (
        parts.scheme not in {"http", "https"}
        or (parts.scheme != "https" and not loopback)
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.path not in {"", "/"}
        or parts.query
        or parts.fragment
        or (port is not None and not 1 <= port <= 65_535)
    ):
        raise ValueError("base URL must be HTTPS (or loopback HTTP) without credentials or a path")
    return base


def fetch(url: str, data: dict[str, Any] | None = None) -> tuple[int, Any, bytes]:
    body = None if data is None else json.dumps(data, separators=(",", ":")).encode()
    request = urllib.request.Request(  # noqa: S310 -- base URL is validated before calls
        url,
        data=body,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="GET" if data is None else "POST",
    )
    try:
        with _OPENER.open(request, timeout=10) as response:  # noqa: S310
            raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise RuntimeError("response body exceeds probe limit")
            return response.status, json.loads(raw), raw
    except urllib.error.HTTPError as exc:
        raw = exc.read(200_000)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"detail": "non-JSON error body"}
        return exc.code, payload, raw
    except (
        json.JSONDecodeError,
        OSError,
        RuntimeError,
        TimeoutError,
        urllib.error.URLError,
        ValueError,
    ) as exc:
        return 0, {"detail": f"probe transport failed: {type(exc).__name__}"}, b""


def _verify_source_contract(
    *,
    route: str,
    status: int,
    payload: Any,
    expected_revision: str,
    failures: list[str],
) -> None:
    if status != 200 or not isinstance(payload, dict):
        failures.append(f"{route}: source contract is unavailable")
        return
    build = payload.get("build")
    binding = payload.get("source_binding")
    revision = build.get("revision") if isinstance(build, dict) else None
    if revision != expected_revision:
        failures.append(f"{route}: build revision does not match exact merged revision")
    if not isinstance(build, dict) or build.get("repository") != "szl-holdings/lyte-services":
        failures.append(f"{route}: canonical product repository is not reported")
    if (
        not isinstance(binding, dict)
        or binding.get("bindings_agree") is not True
        or binding.get("product_repository") != "szl-holdings/lyte-services"
        or binding.get("product_revision") != expected_revision
        or binding.get("hub_surface") != "SZLHOLDINGS/lyte"
    ):
        failures.append(f"{route}: source binding does not close")
    if payload.get("effectors_enabled") is not False:
        failures.append(f"{route}: effector boundary is not explicitly disabled")


def _verify_runtime_identity(
    *,
    route: str,
    status: int,
    payload: Any,
    expected_revision: str,
    failures: list[str],
) -> None:
    if status != 200 or not isinstance(payload, dict):
        failures.append(f"{route}: runtime identity is unavailable")
        return
    canonical = "szl-holdings/lyte-services"
    if payload.get("source_repository") != canonical:
        failures.append(f"{route}: source repository is not canonical")
    if payload.get("runtime_repository") != canonical:
        failures.append(f"{route}: runtime repository is not canonical")
    if payload.get("source_revision") != expected_revision:
        failures.append(f"{route}: source revision does not match exact merged revision")
    if payload.get("runtime_source_revision") != expected_revision:
        failures.append(f"{route}: runtime revision does not match exact merged revision")
    if payload.get("effectors_enabled") is not False:
        failures.append(f"{route}: effector boundary is not explicitly disabled")
    if payload.get("human_approval_required") is not True:
        failures.append(f"{route}: human approval boundary is not explicit")


def _canonical_receipt_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8") + b"\n"


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    document: dict[str, Any] = {}
    for key, value in pairs:
        if key in document:
            raise ValueError("duplicate JSON key")
        document[key] = value
    return document


def _verify_build_receipt(
    *,
    status: int,
    payload: Any,
    raw: bytes,
    expected_revision: str,
    expected_receipt_sha256: str,
    failures: list[str],
) -> str | None:
    route = "/build-receipt.json"
    if status != 200 or not isinstance(payload, dict):
        failures.append(f"{route}: immutable receipt is unavailable")
        return None
    try:
        wire_payload = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        failures.append(f"{route}: receipt JSON is malformed or ambiguous")
        return None
    if not isinstance(wire_payload, dict) or wire_payload != payload:
        failures.append(f"{route}: receipt wire document does not match parsed response")
        return None
    canonical = _canonical_receipt_bytes(wire_payload)
    if raw != canonical:
        failures.append(f"{route}: receipt response is not canonical stored bytes")
    if hashlib.sha256(raw).hexdigest() != expected_receipt_sha256:
        failures.append(f"{route}: receipt digest does not match independent publication evidence")
    if payload.get("schema") != "szl.lyte-build-receipt/v1":
        failures.append(f"{route}: receipt schema is not supported")
    if payload.get("service") != "lyte-signal-lattice":
        failures.append(f"{route}: receipt service identity is invalid")
    source = payload.get("source")
    if not isinstance(source, dict) or source != {
        "repository": "szl-holdings/lyte-services",
        "revision": expected_revision,
    }:
        failures.append(f"{route}: receipt source binding does not match the release")
    manifest = payload.get("payload")
    payload_sha256: str | None = None
    if not isinstance(manifest, dict):
        failures.append(f"{route}: payload manifest is unavailable")
    else:
        payload_sha256 = manifest.get("sha256")
        if (
            manifest.get("scope") != "lyte-application-files/v1"
            or manifest.get("algorithm") != "sha256"
            or not isinstance(manifest.get("files"), list)
            or not manifest["files"]
            or not isinstance(payload_sha256, str)
            or _SHA256.fullmatch(payload_sha256) is None
        ):
            failures.append(f"{route}: payload manifest contract is invalid")
    return payload_sha256


def _verify_live_contract(
    *,
    route: str,
    status: int,
    payload: Any,
    expected_revision: str,
    expected_receipt_sha256: str,
    expected_payload_sha256: str | None,
    failures: list[str],
) -> tuple[str | None, str | None]:
    if status != 200 or not isinstance(payload, dict):
        failures.append(f"{route}: live application attestation is unavailable")
        return None, None
    if payload.get("schema") != "szl.lyte-live/v1":
        failures.append(f"{route}: live schema is not supported")
    if payload.get("observation_state") != "VERIFIED" or payload.get("ready") is not True:
        failures.append(f"{route}: live observation did not verify")
    _verify_runtime_identity(
        route=route,
        status=status,
        payload=payload,
        expected_revision=expected_revision,
        failures=failures,
    )
    checks = payload.get("checks")
    if not isinstance(checks, dict) or any(
        checks.get(name) != "READY"
        for name in ("source_binding", "public_routes", "build_receipt", "database")
    ):
        failures.append(f"{route}: required live checks are not ready")
    receipt = payload.get("build_receipt")
    if not isinstance(receipt, dict):
        failures.append(f"{route}: receipt observation is unavailable")
        return None, None
    live_receipt_sha256 = receipt.get("receipt_sha256")
    live_payload_sha256 = receipt.get("payload_sha256")
    if (
        receipt.get("state") != "VERIFIED"
        or receipt.get("valid") is not True
        or receipt.get("required") is not True
        or receipt.get("source_revision") != expected_revision
        or live_receipt_sha256 != expected_receipt_sha256
    ):
        failures.append(f"{route}: receipt observation does not match publication evidence")
    if expected_payload_sha256 is not None and live_payload_sha256 != expected_payload_sha256:
        failures.append(f"{route}: payload digest disagrees with the immutable receipt")
    return (
        live_receipt_sha256 if isinstance(live_receipt_sha256, str) else None,
        live_payload_sha256 if isinstance(live_payload_sha256, str) else None,
    )


def probe(
    base: str,
    expected_revision: str,
    expected_receipt_sha256: str,
) -> dict[str, Any]:
    routes: dict[str, Any] = {}
    failures: list[str] = []
    expected_revision = expected_revision.strip().lower()
    expected_receipt_sha256 = expected_receipt_sha256.strip().lower()
    if re.fullmatch(r"[0-9a-f]{40}", expected_revision) is None:
        failures.append("expected revision must be an exact 40-character lowercase Git SHA")
        return {
            "schema": "szl.lyte-live-probe/v3",
            "base_url": base,
            "expected_revision": expected_revision,
            "expected_receipt_sha256": expected_receipt_sha256,
            "routes": routes,
            "effectors_enabled": False,
            "secret_values_recorded": False,
            "independent_receipt_binding": False,
            "complete": False,
            "failures": failures,
        }
    if _SHA256.fullmatch(expected_receipt_sha256) is None:
        failures.append("expected receipt digest must be 64 lowercase hexadecimal characters")
        return {
            "schema": "szl.lyte-live-probe/v3",
            "base_url": base,
            "expected_revision": expected_revision,
            "expected_receipt_sha256": expected_receipt_sha256,
            "routes": routes,
            "effectors_enabled": False,
            "secret_values_recorded": False,
            "independent_receipt_binding": False,
            "complete": False,
            "failures": failures,
        }
    payloads: dict[str, Any] = {}
    raw_payloads: dict[str, bytes] = {}
    for route in READ_ROUTES:
        status, payload, raw = fetch(base + route)
        payloads[route] = payload
        raw_payloads[route] = raw
        routes[route] = {"status": status}
        if status != 200:
            failures.append(f"{route}: expected 200, received {status}")
        if not isinstance(payload, dict):
            failures.append(f"{route}: expected a JSON object")
            continue
        if payload.get("effectors_enabled") is True:
            failures.append(f"{route}: effectors were reported enabled")
        if route == "/healthz" and payload.get("ok") is not True:
            failures.append("/healthz: ok was not true")
        if route == "/readyz" and payload.get("ready") is not True:
            failures.append("/readyz: ready was not true")
    for route in (
        "/healthz",
        "/readyz",
        "/api/live",
        "/api/build-info",
        "/api/source",
        "/.well-known/szl-source.json",
    ):
        _verify_runtime_identity(
            route=route,
            status=routes[route]["status"],
            payload=payloads[route],
            expected_revision=expected_revision,
            failures=failures,
        )
    receipt_payload_sha256 = _verify_build_receipt(
        status=routes["/build-receipt.json"]["status"],
        payload=payloads["/build-receipt.json"],
        raw=raw_payloads["/build-receipt.json"],
        expected_revision=expected_revision,
        expected_receipt_sha256=expected_receipt_sha256,
        failures=failures,
    )
    first_receipt_sha256, first_payload_sha256 = _verify_live_contract(
        route="/api/live",
        status=routes["/api/live"]["status"],
        payload=payloads["/api/live"],
        expected_revision=expected_revision,
        expected_receipt_sha256=expected_receipt_sha256,
        expected_payload_sha256=receipt_payload_sha256,
        failures=failures,
    )
    for route in ("/api/build-info", "/api/source", "/.well-known/szl-source.json"):
        _verify_source_contract(
            route=route,
            status=routes[route]["status"],
            payload=payloads[route],
            expected_revision=expected_revision,
            failures=failures,
        )
    for route, payload in {
        "/api/lyte/v2/ask": {"question": "Why is checkout revenue at risk?"},
        "/api/lyte/v2/hatun/evaluate": {
            "action_type": "simulated rollback review",
            "evidence_labels": ["SAMPLE"],
            "requests_execution": False,
        },
    }.items():
        status, response_payload, _ = fetch(base + route, payload)
        routes[route] = {"status": status}
        if status != 200:
            failures.append(f"{route}: expected 200, received {status}")
        if not isinstance(response_payload, dict):
            failures.append(f"{route}: expected a JSON object")
            continue
        if response_payload.get("can_execute") is not False:
            failures.append(f"{route}: can_execute was not explicitly false")
        if response_payload.get("effectors_enabled") is True:
            failures.append(f"{route}: effectors were reported enabled")
        if route.endswith("hatun/evaluate") and response_payload.get("decision") not in {
            "REVIEW",
            "ABSTAIN",
            "DENY",
        }:
            failures.append(f"{route}: decision escaped the human-sovereign boundary")
    for route in DENIED_MUTATION_ROUTES:
        status, _, _ = fetch(base + route, {})
        routes[route] = {"status": status, "expected": "anonymous mutation denied"}
        if status not in {401, 403, 503}:
            failures.append(f"{route}: anonymous mutation did not fail closed")
    second_status, second_live, _ = fetch(base + "/api/live")
    routes["/api/live#post"] = {"status": second_status}
    second_receipt_sha256, second_payload_sha256 = _verify_live_contract(
        route="/api/live#post",
        status=second_status,
        payload=second_live,
        expected_revision=expected_revision,
        expected_receipt_sha256=expected_receipt_sha256,
        expected_payload_sha256=receipt_payload_sha256,
        failures=failures,
    )
    if (first_receipt_sha256, first_payload_sha256) != (
        second_receipt_sha256,
        second_payload_sha256,
    ):
        failures.append("/api/live: deployment identity changed during the proof sequence")
    return {
        "schema": "szl.lyte-live-probe/v3",
        "base_url": base,
        "expected_revision": expected_revision,
        "expected_receipt_sha256": expected_receipt_sha256,
        "routes": routes,
        "effectors_enabled": False,
        "secret_values_recorded": False,
        "independent_receipt_binding": True,
        "complete": not failures,
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="https://szlholdings-lyte.hf.space")
    parser.add_argument("--revision", required=True)
    parser.add_argument("--receipt-sha256", required=True)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--wait-seconds", type=int, default=10)
    args = parser.parse_args()
    try:
        base = validate_base_url(args.base_url)
    except ValueError as exc:
        print(json.dumps({"complete": False, "failures": [str(exc)]}, indent=2, sort_keys=True))
        return 1
    result: dict[str, Any] = {}
    attempts = max(1, min(args.retries, 120))
    for attempt in range(1, attempts + 1):
        result = probe(base, args.revision, args.receipt_sha256)
        result["attempt"] = attempt
        if result["complete"]:
            break
        if attempt < attempts:
            time.sleep(max(1, min(args.wait_seconds, 60)))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("complete") else 1


if __name__ == "__main__":
    raise SystemExit(main())
