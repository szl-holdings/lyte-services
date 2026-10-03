"""Build-time payload receipt and live-attestation contract tests."""

from __future__ import annotations

import hashlib
import json
import os
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lyte.api import routes_health
from lyte.app import create_app
from lyte.build_receipt import (
    PAYLOAD_FILES,
    PAYLOAD_ROOTS,
    SCHEMA,
    build_receipt_document,
    generate_build_receipt,
    observe_build_receipt,
)

REVISION = "a" * 40


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()


def _payload_root(tmp_path: Path) -> Path:
    root = tmp_path / "app"
    root.mkdir()
    for name in PAYLOAD_ROOTS:
        directory = root / name
        directory.mkdir()
        (directory / "payload.txt").write_text(f"{name}\n", encoding="utf-8")
    for name in PAYLOAD_FILES:
        value = f"fixture:{name}\n"
        if name == "source_revision.txt":
            value = f"{REVISION}\n"
        (root / name).write_text(value, encoding="utf-8")
    return root


def _runtime_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LYTE_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    monkeypatch.setenv("LYTE_DEMO_MODE", "true")
    monkeypatch.setenv("LYTE_SOURCE_REVISION", REVISION)
    monkeypatch.setenv("LYTE_REQUIRE_SOURCE_BINDING", "true")
    for name in (
        "SOURCE_REVISION",
        "GITHUB_SHA",
        "SPACE_ID",
        "LYTE_REQUIRE_BUILD_RECEIPT",
        "LYTE_DEV_AUTH_ENABLED",
        "LYTE_DEV_AUTH_TOKEN",
        "LYTE_WEBHOOK_HMAC_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)


def _write_document(root: Path, document: dict[str, object]) -> None:
    (root / "build-receipt.json").write_bytes(_canonical(document) + b"\n")


def _rebind_manifest_digest(document: dict[str, object]) -> None:
    payload = document["payload"]
    assert isinstance(payload, dict)
    basis = deepcopy(document)
    basis_payload = basis["payload"]
    assert isinstance(basis_payload, dict)
    basis_payload.pop("sha256")
    payload["sha256"] = hashlib.sha256(_canonical(basis)).hexdigest()


def test_generation_is_deterministic_and_verifies_closed_payload(tmp_path: Path) -> None:
    root = _payload_root(tmp_path)
    first = generate_build_receipt(root, source_revision=REVISION)
    first_bytes = (root / "build-receipt.json").read_bytes()
    assert first.valid is True
    assert first.state == "VERIFIED"
    assert first.source_revision == REVISION
    assert len(first.receipt_sha256 or "") == 64
    assert len(first.payload_sha256 or "") == 64

    (root / "build-receipt.json").unlink()
    second = generate_build_receipt(root, source_revision=REVISION)
    assert second.valid is True
    assert (root / "build-receipt.json").read_bytes() == first_bytes


def test_generation_uses_validated_marker_without_a_build_argument(tmp_path: Path) -> None:
    """Provider builds may stamp source_revision.txt without setting a Docker ARG."""

    root = _payload_root(tmp_path)
    observation = generate_build_receipt(root)
    assert observation.valid is True
    assert observation.source_revision == REVISION


@pytest.mark.parametrize("mutation", ("change", "addition", "deletion", "marker"))
def test_fresh_verification_detects_every_payload_set_or_byte_change(
    mutation: str,
    tmp_path: Path,
) -> None:
    root = _payload_root(tmp_path)
    generate_build_receipt(root, source_revision=REVISION)
    target = root / "lyte" / "payload.txt"
    if mutation == "change":
        target.write_text("changed\n", encoding="utf-8")
    elif mutation == "addition":
        (root / "lyte" / "unreceipted.py").write_text("raise SystemExit\n", encoding="utf-8")
    elif mutation == "deletion":
        target.unlink()
    else:
        (root / "source_revision.txt").write_text(f"{'b' * 40}\n", encoding="utf-8")
    observed = observe_build_receipt(
        root,
        expected_source_revision=REVISION,
        required=True,
    )
    assert observed.valid is False
    assert observed.state == "PAYLOAD_MISMATCH"


def test_forged_incomplete_manifest_is_rejected_even_with_recomputed_digest(
    tmp_path: Path,
) -> None:
    root = _payload_root(tmp_path)
    document = build_receipt_document(root, source_revision=REVISION)
    payload = document["payload"]
    assert isinstance(payload, dict)
    files = payload["files"]
    assert isinstance(files, list)
    files.pop()
    _rebind_manifest_digest(document)
    _write_document(root, document)
    observed = observe_build_receipt(
        root,
        expected_source_revision=REVISION,
        required=True,
    )
    assert observed.state == "PAYLOAD_MISMATCH"
    assert observed.valid is False


@pytest.mark.parametrize("corruption", ("traversal", "boolean-size", "unsupported-schema"))
def test_malformed_or_ambiguous_receipt_contracts_fail_closed(
    corruption: str,
    tmp_path: Path,
) -> None:
    root = _payload_root(tmp_path)
    document = build_receipt_document(root, source_revision=REVISION)
    payload = document["payload"]
    assert isinstance(payload, dict)
    files = payload["files"]
    assert isinstance(files, list) and isinstance(files[0], dict)
    if corruption == "traversal":
        files[0]["path"] = "../escape"
    elif corruption == "boolean-size":
        files[0]["size"] = True
    else:
        document["schema"] = "szl.lyte-build-receipt/future"
    _rebind_manifest_digest(document)
    _write_document(root, document)
    observed = observe_build_receipt(
        root,
        expected_source_revision=REVISION,
        required=True,
    )
    assert observed.state == "INVALID"
    assert observed.valid is False


def test_duplicate_json_keys_and_symlinks_are_rejected(tmp_path: Path) -> None:
    root = _payload_root(tmp_path)
    (root / "build-receipt.json").write_bytes(
        b'{"schema":"one","schema":"two"}\n'
    )
    duplicate = observe_build_receipt(
        root,
        expected_source_revision=REVISION,
        required=True,
    )
    assert duplicate.state == "INVALID"

    (root / "build-receipt.json").unlink()
    link = root / "lyte" / "linked.txt"
    try:
        os.symlink(root / "README.md", link)
    except OSError:
        pytest.skip("symlink creation is unavailable on this host")
    with pytest.raises(Exception, match="symlinks are forbidden"):
        generate_build_receipt(root, source_revision=REVISION)


def test_local_absence_is_explicit_but_live_attestation_requires_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _payload_root(tmp_path)
    _runtime_environment(monkeypatch)
    monkeypatch.setattr(routes_health, "_ROOT", root)
    with TestClient(create_app()) as client:
        ready = client.get("/readyz")
        assert ready.status_code == 200
        assert ready.json()["checks"]["build_receipt"] == "MISSING_OPTIONAL_LOCAL"
        assert client.get("/api/live").status_code == 503
        assert client.get("/build-receipt.json").status_code == 503

    monkeypatch.setenv("LYTE_REQUIRE_BUILD_RECEIPT", "true")
    with TestClient(create_app()) as client:
        required = client.get("/readyz")
        assert required.status_code == 503
        assert required.json()["checks"]["build_receipt"] == "MISSING"


def test_hosted_space_cannot_disable_receipt_requirement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _payload_root(tmp_path)
    _runtime_environment(monkeypatch)
    monkeypatch.setenv("SPACE_ID", "SZLHOLDINGS/lyte")
    monkeypatch.setenv("LYTE_REQUIRE_BUILD_RECEIPT", "false")
    monkeypatch.setattr(routes_health, "_ROOT", root)
    with TestClient(create_app()) as client:
        assert client.app.state.runtime.require_build_receipt is True
        assert client.get("/readyz").status_code == 503


def test_valid_receipt_closes_readiness_live_and_exact_receipt_route(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _payload_root(tmp_path)
    expected = generate_build_receipt(root, source_revision=REVISION)
    stored = (root / "build-receipt.json").read_bytes()
    _runtime_environment(monkeypatch)
    monkeypatch.setenv("LYTE_REQUIRE_BUILD_RECEIPT", "true")
    monkeypatch.setattr(routes_health, "_ROOT", root)
    with TestClient(create_app()) as client:
        ready = client.get("/readyz")
        live = client.get("/api/live")
        receipt = client.get("/build-receipt.json")
        assert ready.status_code == live.status_code == receipt.status_code == 200
        assert ready.json()["checks"]["public_routes"] == "READY"
        assert ready.json()["checks"]["build_receipt"] == "READY"
        assert live.json()["observation_state"] == "VERIFIED"
        assert live.json()["build_receipt"]["receipt_sha256"] == expected.receipt_sha256
        assert live.json()["build_receipt"]["payload_sha256"] == expected.payload_sha256
        assert live.json()["data_mode"] == "SAMPLE"
        assert live.json()["effectors_enabled"] is False
        assert receipt.content == stored
        assert receipt.headers["cache-control"] == "no-store"

        (root / "lyte" / "payload.txt").write_text("runtime mutation\n", encoding="utf-8")
        assert client.get("/readyz").status_code == 503
        assert client.get("/api/live").status_code == 503
        assert client.get("/build-receipt.json").status_code == 503


def test_invalid_present_receipt_fails_even_when_local_absence_is_optional(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _payload_root(tmp_path)
    (root / "build-receipt.json").write_text("not-json\n", encoding="utf-8")
    _runtime_environment(monkeypatch)
    monkeypatch.setattr(routes_health, "_ROOT", root)
    with TestClient(create_app()) as client:
        ready = client.get("/readyz")
        assert ready.status_code == 503
        assert ready.json()["checks"]["build_receipt"] == "INVALID"


def test_operational_routes_do_not_mint_business_receipts_or_grant_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _payload_root(tmp_path)
    generate_build_receipt(root, source_revision=REVISION)
    _runtime_environment(monkeypatch)
    monkeypatch.setenv("LYTE_REQUIRE_BUILD_RECEIPT", "true")
    monkeypatch.setattr(routes_health, "_ROOT", root)
    with TestClient(create_app()) as client:
        before = client.get("/api/lyte/v2/receipts").json()["count"]
        for route in ("/readyz", "/api/live", "/build-receipt.json"):
            assert client.get(route).status_code == 200
            assert client.post(route, json={}).status_code == 405
        assert client.get("/api/lyte/v2/receipts").json()["count"] == before
        assert client.app.state.runtime.effectors_enabled is False


def test_operational_contract_is_advertised_by_the_canonical_app() -> None:
    assert {"/readyz", "/api/live", "/build-receipt.json"} <= set(create_app().openapi()["paths"])
    assert SCHEMA == "szl.lyte-build-receipt/v1"
