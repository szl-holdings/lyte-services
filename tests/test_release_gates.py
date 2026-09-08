"""Focused release, source-binding, and legacy compatibility gates."""

from __future__ import annotations

import importlib
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
    assert text.count("python -m pytest") == 5
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


def _fake_live_fetch(expected: str, *, stale: bool = False):
    def fetch(url: str, data: dict[str, Any] | None = None) -> tuple[int, Any]:
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
        if route in {"/api/build-info", "/api/source", "/.well-known/szl-source.json"}:
            return 200, {
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
            }
        if route == "/healthz":
            return 200, {"ok": True, **identity}
        if route == "/readyz":
            return 200, {"ready": True, **identity}
        if data is not None and route.endswith("hatun/evaluate"):
            return 200, {"decision": "REVIEW", "can_execute": False, "effectors_enabled": False}
        if data is not None and route.endswith("ask"):
            return 200, {"can_execute": False, "effectors_enabled": False}
        if data is not None:
            return 401, {"detail": "authentication required"}
        return 200, {"effectors_enabled": False}

    return fetch


def test_live_probe_closes_all_source_surfaces(monkeypatch: pytest.MonkeyPatch) -> None:
    revision = "a" * 40
    monkeypatch.setattr(probe_live, "fetch", _fake_live_fetch(revision))
    result = probe_live.probe("https://example.invalid", revision)
    assert result["complete"] is True
    monkeypatch.setattr(probe_live, "fetch", _fake_live_fetch(revision, stale=True))
    stale = probe_live.probe("https://example.invalid", revision)
    assert stale["complete"] is False
    assert any("exact merged revision" in failure for failure in stale["failures"])


def test_live_probe_rejects_unsafe_base_urls_and_invalid_revisions() -> None:
    assert probe_live.validate_base_url("http://127.0.0.1:7860") == "http://127.0.0.1:7860"
    with pytest.raises(ValueError):
        probe_live.validate_base_url("http://public.example/path")
    result = probe_live.probe("https://example.invalid", "main")
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
