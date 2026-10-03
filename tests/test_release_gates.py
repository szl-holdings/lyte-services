"""Focused release, source-binding, and legacy compatibility gates."""

from __future__ import annotations

import hashlib
import importlib
import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from alembic import command
from alembic.config import Config
from pydantic import ValidationError
from sqlalchemy import create_engine, text

from tools import probe_live
from tools import szl_product_frontier as frontier
from tools.verify_release_scaffolding import IMMUTABLE_ACTION, verify
from tools.verify_source_binding import is_canonical_remote

ROOT = Path(__file__).resolve().parents[1]


def test_sqlite_migration_persists_head_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = (tmp_path / "migration.db").as_posix()
    database_url = f"sqlite+pysqlite:///{database_path}"
    monkeypatch.setenv("LYTE_ENV", "development")
    monkeypatch.setenv("DATABASE_URL", database_url)
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
        assert revision == "20260904_0001"
    finally:
        engine.dispose()


def test_legacy_import_surfaces_are_real_and_strict() -> None:
    engine = importlib.import_module("lyte_engine")
    api = importlib.import_module("lyte_api")
    token = "a" * 32
    scope = engine.session_scope(token)
    assert scope != token
    assert re.fullmatch(r"[0-9a-f]{64}", scope)
    first = engine.observation_receipt(
        scope=scope,
        kind="compatibility-test",
        payload={"value": 1},
        truth_label="MEASURED",
    )
    second = engine.observation_receipt(
        scope=scope,
        kind="compatibility-test",
        payload={"value": 1},
        truth_label="MEASURED",
    )
    assert first == second
    assert first["raw_session_token_recorded"] is False
    assert first["can_authorize"] is False
    assert api.AskRequest(question="  what   changed?  ").question == "what changed?"
    with pytest.raises(ValidationError):
        api.AskRequest(question="valid", unexpected=True)


def test_workflow_yaml_is_valid_and_action_refs_are_immutable() -> None:
    path = ROOT / ".github" / "workflows" / "compiler.yml"
    parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(parsed["jobs"], dict)
    assert "container-smoke" in parsed["jobs"]
    text = path.read_text(encoding="utf-8")
    references = re.findall(r"^\s*-\s+uses:\s*([^\s]+)\s*$", text, re.MULTILINE)
    assert references
    assert all(
        reference.startswith("./") or IMMUTABLE_ACTION.fullmatch(reference)
        for reference in references
    )
    assert "run: pytest" not in text
    assert "python tools/" not in text
    assert text.count("python -m pytest") == 6
    assert text.count("--editable .") >= 9
    assert "python -m tools.verify_source_binding" in text
    assert verify() == []


@pytest.mark.parametrize(
    "remote",
    [
        "https://github.com/szl-holdings/lyte-services",
        "https://github.com/szl-holdings/lyte-services.git",
        "git@github.com:szl-holdings/lyte-services",
        "git@github.com:szl-holdings/lyte-services.git",
        "HTTPS://GITHUB.COM/SZL-HOLDINGS/LYTE-SERVICES.GIT",
        "https://github.com/szl-holdings/lyte-services.git\n",
    ],
)
def test_source_binding_accepts_only_canonical_remote_shapes(remote: str) -> None:
    assert is_canonical_remote(remote) is True


@pytest.mark.parametrize(
    "remote",
    [
        "http://github.com/szl-holdings/lyte-services.git",
        "https://token@github.com/szl-holdings/lyte-services.git",
        "https://@github.com/szl-holdings/lyte-services.git",
        "https://github.example/szl-holdings/lyte-services.git",
        "https://github.com:/szl-holdings/lyte-services.git",
        "https://github.com:443/szl-holdings/lyte-services.git",
        "https://github.com:8443/szl-holdings/lyte-services.git",
        "https://github.com:notaport/szl-holdings/lyte-services.git",
        "https://github.com.evil/szl-holdings/lyte-services.git",
        "https://github.com./szl-holdings/lyte-services.git",
        "https://github.com/szl-holdings/lyte-services.git?ref=main",
        "https://github.com/szl-holdings/lyte-services.git#main",
        "https://github.com/szl-holdings/lyte-services.git/",
        "https://github.com/szl-holdings//lyte-services.git",
        "https://github.com/szl-holdings/../lyte-services.git",
        "https://github.com/szl-holdings/lyte-services%2egit",
        "https://github.com/szl-holdings%2flyte-services.git",
        "https://github.com/szl-holdings/lyte-services-extra.git",
        "https://github.com/szl-holdings/other.git",
        "git@github.com:szl-holdings/lyte-ſervices.git",
        "root@github.com:szl-holdings/lyte-services.git",
        "git@github.com:szl-holdings/other.git",
        "ssh://git@github.com/szl-holdings/lyte-services.git",
        "git@github.com:szl-holdings/lyte-services.git/extra",
        "not-a-remote",
    ],
)
def test_source_binding_rejects_ambiguous_or_unsafe_remotes(remote: str) -> None:
    assert is_canonical_remote(remote) is False


def _fake_build_receipt(revision: str) -> dict[str, Any]:
    return {
        "schema": "szl.lyte-build-receipt/v1",
        "service": "lyte-signal-lattice",
        "source": {
            "repository": "szl-holdings/lyte-services",
            "revision": revision,
        },
        "payload": {
            "scope": "lyte-application-files/v1",
            "algorithm": "sha256",
            "files": [{"path": "lyte/app.py", "size": 1, "sha256": "d" * 64}],
            "sha256": "e" * 64,
        },
    }


def _fake_receipt_sha256(revision: str) -> str:
    encoded = json.dumps(
        _fake_build_receipt(revision),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode() + b"\n"
    return hashlib.sha256(encoded).hexdigest()


def _fake_live_fetch(expected: str, *, stale: bool = False):
    def response(status: int, payload: Any) -> tuple[int, Any, bytes]:
        raw = json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode() + b"\n"
        return status, payload, raw

    def fetch(url: str, data: dict[str, Any] | None = None) -> tuple[int, Any, bytes]:
        route = url.removeprefix("https://example.invalid")
        revision = "0" * 40 if stale else expected
        identity = {
            "source_repository": "szl-holdings/lyte-services",
            "source_revision": revision,
            "runtime_repository": "szl-holdings/lyte-services",
            "runtime_source_revision": revision,
            "effectors_enabled": False,
            "human_approval_required": True,
        }
        receipt = _fake_build_receipt(revision)
        receipt_sha256 = _fake_receipt_sha256(revision)
        if route == "/build-receipt.json":
            return response(200, receipt)
        if route == "/api/live":
            return response(200, {
                "schema": "szl.lyte-live/v1",
                "observation_state": "VERIFIED",
                "ready": True,
                **identity,
                "checks": {
                    "source_binding": "READY",
                    "public_routes": "READY",
                    "build_receipt": "READY",
                    "database": "READY",
                },
                "build_receipt": {
                    "state": "VERIFIED",
                    "valid": True,
                    "required": True,
                    "receipt_sha256": receipt_sha256,
                    "payload_sha256": receipt["payload"]["sha256"],
                    "source_revision": revision,
                },
            })
        if route in {"/api/build-info", "/api/source", "/.well-known/szl-source.json"}:
            return response(200, {
                **identity,
                "build": {
                    "revision": revision,
                    "repository": "szl-holdings/lyte-services",
                },
                "source_binding": {
                    "bindings_agree": True,
                    "product_repository": "szl-holdings/lyte-services",
                    "product_revision": revision,
                    "hub_surface": "SZLHOLDINGS/lyte",
                },
            })
        if route == "/healthz":
            return response(200, {"ok": True, **identity})
        if route == "/readyz":
            return response(200, {"ready": True, **identity})
        if data is not None and route.endswith("hatun/evaluate"):
            return response(
                200,
                {"decision": "REVIEW", "can_execute": False, "effectors_enabled": False},
            )
        if data is not None and route.endswith("ask"):
            return response(200, {"can_execute": False, "effectors_enabled": False})
        if data is not None:
            return response(401, {"detail": "authentication required"})
        return response(200, {"effectors_enabled": False})

    return fetch


def test_live_probe_closes_all_source_surfaces(monkeypatch: pytest.MonkeyPatch) -> None:
    revision = "a" * 40
    receipt_sha256 = _fake_receipt_sha256(revision)
    monkeypatch.setattr(probe_live, "fetch", _fake_live_fetch(revision))
    result = probe_live.probe("https://example.invalid", revision, receipt_sha256)
    assert result["complete"] is True
    assert result["independent_receipt_binding"] is True
    monkeypatch.setattr(probe_live, "fetch", _fake_live_fetch(revision, stale=True))
    stale = probe_live.probe("https://example.invalid", revision, receipt_sha256)
    assert stale["complete"] is False
    assert any("exact merged revision" in failure for failure in stale["failures"])


@pytest.mark.parametrize(
    ("wire_mode", "expected_failure"),
    (
        ("pretty", "not canonical stored bytes"),
        ("duplicate", "malformed or ambiguous"),
    ),
)
def test_live_probe_rejects_noncanonical_or_ambiguous_receipt_wire_bytes(
    monkeypatch: pytest.MonkeyPatch,
    wire_mode: str,
    expected_failure: str,
) -> None:
    revision = "a" * 40
    expected_digest = _fake_receipt_sha256(revision)
    canonical_fetch = _fake_live_fetch(revision)

    def substituted_receipt(
        url: str,
        data: dict[str, Any] | None = None,
    ) -> tuple[int, Any, bytes]:
        status, payload, raw = canonical_fetch(url, data)
        if url.endswith("/build-receipt.json"):
            if wire_mode == "pretty":
                raw = json.dumps(payload, indent=2, sort_keys=True).encode() + b"\n"
            else:
                raw = raw.replace(
                    b'{"payload":',
                    b'{"schema":"szl.lyte-build-receipt/v1","payload":',
                    1,
                )
        return status, payload, raw

    monkeypatch.setattr(probe_live, "fetch", substituted_receipt)
    result = probe_live.probe("https://example.invalid", revision, expected_digest)
    assert result["complete"] is False
    assert any(expected_failure in failure for failure in result["failures"])
    if wire_mode == "pretty":
        assert any("independent publication evidence" in failure for failure in result["failures"])


def test_live_probe_rejects_unsafe_base_urls_and_invalid_revisions() -> None:
    assert probe_live.validate_base_url("http://127.0.0.1:7860") == "http://127.0.0.1:7860"
    with pytest.raises(ValueError):
        probe_live.validate_base_url("http://public.example/path")
    result = probe_live.probe("https://example.invalid", "main", "bad-digest")
    assert result["complete"] is False
    assert result["routes"] == {}


def test_release_revision_never_falls_back_to_feature_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revision = "b" * 40
    monkeypatch.setattr(frontier, "remote_main", lambda: revision)
    args = SimpleNamespace(source_revision=None)
    receipt = frontier.RunReceipt(product="lyte", command="deploy")
    assert frontier.resolve_release_revision(receipt, args) is None
    assert any("--source-revision" in failure for failure in receipt.failures)


def test_release_revision_must_equal_current_remote_main(monkeypatch: pytest.MonkeyPatch) -> None:
    revision = "c" * 40
    monkeypatch.setattr(frontier, "remote_main", lambda: revision)
    args = SimpleNamespace(source_revision=revision)
    receipt = frontier.RunReceipt(product="lyte", command="deploy")
    assert frontier.resolve_release_revision(receipt, args) == revision
    stale_args = SimpleNamespace(source_revision="d" * 40)
    stale_receipt = frontier.RunReceipt(product="lyte", command="deploy")
    assert frontier.resolve_release_revision(stale_receipt, stale_args) is None
    assert "exact current remote main" in stale_receipt.failures[-1]


def test_live_verification_requires_independent_receipt_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revision = "c" * 40
    monkeypatch.setattr(frontier, "remote_main", lambda: revision)
    args = SimpleNamespace(
        source_revision=revision,
        receipt_sha256=None,
    )
    receipt = frontier.RunReceipt(product="lyte", command="verify")
    assert frontier.verify(receipt, args) is False
    assert "independent publication evidence" in receipt.failures[-1]


def test_deploy_dispatches_only_current_lyte_scoped_canonical_writer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revision = "c" * 40
    commands = []
    monkeypatch.setattr(frontier, "remote_main", lambda: revision)

    def execute(argv: list[str]) -> frontier.CommandResult:
        commands.append(argv)
        return frontier.CommandResult(argv, 0)

    monkeypatch.setattr(frontier, "execute", execute)
    args = SimpleNamespace(
        source_revision=revision, no_deploy=False, read_only=False, dry_run=False
    )
    receipt = frontier.RunReceipt(product="lyte", command="deploy")
    assert frontier.deploy(receipt, args) is True
    assert commands == [[
        "gh", "workflow", "run", "hf-publish-vertical-flagships.yml", "--repo",
        "szl-holdings/a11oy", "--ref", "main", "--field", "scope=lyte",
    ]]
    dispatched = receipt.deployments["SZLHOLDINGS/lyte"]
    assert dispatched["requested_source_revision"] == revision
    assert "source_revision" not in dispatched
    assert dispatched["evidence_class"] == "DECLARED"
    assert dispatched["publication_verified"] is False


def test_deploy_refuses_stale_revision_without_any_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(frontier, "remote_main", lambda: "d" * 40)
    monkeypatch.setattr(frontier, "execute", lambda *_: pytest.fail("must not dispatch"))
    args = SimpleNamespace(
        source_revision="c" * 40, no_deploy=False, read_only=False, dry_run=False
    )
    receipt = frontier.RunReceipt(product="lyte", command="deploy")
    assert frontier.deploy(receipt, args) is False
    assert receipt.deployments == {}


def test_dispatch_only_receipt_never_reports_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(frontier, "RUNS", tmp_path)
    monkeypatch.setattr(frontier, "artifact_digests", dict)
    receipt = frontier.RunReceipt(product="lyte", command="deploy")
    receipt.deployments["SZLHOLDINGS/lyte"] = {
        "requested_source_revision": "c" * 40,
        "publication_verified": False,
    }
    assert frontier.finish(receipt, True, True) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["complete"] is False
    assert "dispatch is not release closure" in payload["failures"][-1]


def test_resume_receipt_requires_one_consistent_release_revision() -> None:
    revision = "e" * 40
    previous = {
        "source_revisions": {"release": revision},
        "pull_requests": {frontier.REPOSITORY: {"mergeCommit": {"oid": revision}}},
        "deployments": {"SZLHOLDINGS/lyte": {"source_revision": revision}},
    }
    assert frontier.resume_release_revision(previous) == revision
    previous["deployments"]["SZLHOLDINGS/lyte"]["source_revision"] = "f" * 40
    with pytest.raises(ValueError):
        frontier.resume_release_revision(previous)
