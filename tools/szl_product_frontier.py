#!/usr/bin/env python3
"""Fail-closed control plane for the SZL product frontier.

The controller orchestrates local gates and protected GitHub workflows. It does
not contain or accept Hugging Face token values and never writes directly to a
Space. All subprocesses use argument arrays with ``shell=False``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "artifacts" / "runs"
SCHEMA = "szl.product-frontier-run/v1"
REPOSITORY = "szl-holdings/lyte-services"
PUBLISHER_REPOSITORY = "szl-holdings/a11oy"
PUBLISHER_WORKFLOW = "hf-sync.yml"
SHA = re.compile(r"^[0-9a-f]{40}$")
SECRET_PATTERNS = (
    re.compile(r"gh[pousr]_[A-Za-z0-9]{12,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{12,}"),
    re.compile(r"hf_[A-Za-z0-9]{12,}"),
    re.compile(r"sk-(?:live|test|proj)-[A-Za-z0-9_-]{12,}"),
)


def now() -> str:
    return dt.datetime.now(dt.UTC).isoformat().replace("+00:00", "Z")


def redact(value: str) -> str:
    clean = value
    for pattern in SECRET_PATTERNS:
        clean = pattern.sub("[REDACTED]", clean)
    return clean


@dataclass(slots=True)
class CommandResult:
    argv: list[str]
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def passed(self) -> bool:
        return self.returncode == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "argv": self.argv,
            "returncode": self.returncode,
            "passed": self.passed,
            "stdout": redact(self.stdout)[-4_000:],
            "stderr": redact(self.stderr)[-4_000:],
        }


@dataclass(slots=True)
class RunReceipt:
    product: str
    command: str
    started_at: str = field(default_factory=now)
    completed_at: str | None = None
    repositories: dict[str, Any] = field(default_factory=dict)
    source_revisions: dict[str, Any] = field(default_factory=dict)
    branches: dict[str, Any] = field(default_factory=dict)
    pull_requests: dict[str, Any] = field(default_factory=dict)
    checks: dict[str, Any] = field(default_factory=dict)
    containers: dict[str, Any] = field(default_factory=dict)
    deployments: dict[str, Any] = field(default_factory=dict)
    live_routes: dict[str, Any] = field(default_factory=dict)
    artifact_sha256: dict[str, str] = field(default_factory=dict)
    secret_values_recorded: bool = False
    raw_session_tokens_recorded: bool = False
    delete_operations: int = 0
    effectors_enabled: bool = False
    complete: bool = False
    failures: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"schema": SCHEMA, **asdict(self)}


def execute(
    argv: Sequence[str],
    *,
    cwd: Path = ROOT,
    env: dict[str, str] | None = None,
    timeout: int = 900,
    quiet: bool = False,
) -> CommandResult:
    merged = os.environ.copy()
    if env:
        merged.update(env)
    try:
        result = subprocess.run(  # noqa: S603
            list(argv),
            cwd=cwd,
            env=merged,
            check=False,
            stdout=subprocess.DEVNULL if quiet else subprocess.PIPE,
            stderr=subprocess.DEVNULL if quiet else subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            shell=False,
        )
        return CommandResult(
            list(argv),
            result.returncode,
            "" if quiet else result.stdout,
            "" if quiet else result.stderr,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CommandResult(list(argv), 127, "", type(exc).__name__)


def git(*args: str) -> CommandResult:
    return execute(["git", *args])


def checked(receipt: RunReceipt, name: str, result: CommandResult) -> bool:
    receipt.checks[name] = result.to_dict()
    if not result.passed:
        receipt.failures.append(f"{name} failed with exit {result.returncode}")
    return result.passed


def command_available(name: str) -> bool:
    return shutil.which(name) is not None


def current_head() -> str | None:
    result = git("rev-parse", "HEAD")
    value = result.stdout.strip().lower()
    return value if result.passed and SHA.fullmatch(value) else None


def current_branch() -> str | None:
    result = git("branch", "--show-current")
    return result.stdout.strip() if result.passed and result.stdout.strip() else None


def remote_main() -> str | None:
    result = git("ls-remote", "origin", "refs/heads/main")
    value = result.stdout.split()[0].lower() if result.passed and result.stdout.split() else ""
    return value if SHA.fullmatch(value) else None


def remote_branch(branch: str) -> str | None:
    result = git("ls-remote", "origin", f"refs/heads/{branch}")
    value = result.stdout.split()[0].lower() if result.passed and result.stdout.split() else ""
    return value if SHA.fullmatch(value) else None


def pull_request_merge_revision(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    merge_commit = payload.get("mergeCommit")
    value = merge_commit.get("oid", "") if isinstance(merge_commit, dict) else ""
    normalized = str(value).strip().lower()
    return normalized if SHA.fullmatch(normalized) else None


def resume_release_revision(previous: dict[str, Any]) -> str | None:
    candidates: list[str] = []
    revisions = previous.get("source_revisions")
    if isinstance(revisions, dict) and revisions.get("release"):
        candidates.append(str(revisions["release"]).strip().lower())
    pull_requests = previous.get("pull_requests")
    if isinstance(pull_requests, dict):
        merged = pull_request_merge_revision(pull_requests.get(REPOSITORY))
        if merged:
            candidates.append(merged)
    deployments = previous.get("deployments")
    if isinstance(deployments, dict):
        deployment = deployments.get("SZLHOLDINGS/lyte")
        if isinstance(deployment, dict) and deployment.get("source_revision"):
            candidates.append(str(deployment["source_revision"]).strip().lower())
    distinct = set(candidates)
    if not distinct:
        return None
    if len(distinct) != 1 or not all(SHA.fullmatch(value) for value in distinct):
        raise ValueError("resume receipt release revisions are invalid or disagree")
    return distinct.pop()


def resolve_release_revision(receipt: RunReceipt, args: argparse.Namespace) -> str | None:
    """Resolve one release revision and prove it is the current remote main."""

    candidates: dict[str, str] = {}
    explicit = str(args.source_revision or "").strip().lower()
    if explicit:
        candidates["explicit"] = explicit
    merged = pull_request_merge_revision(receipt.pull_requests.get(REPOSITORY))
    if merged:
        candidates["merged_pull_request"] = merged
    deployed = receipt.deployments.get("SZLHOLDINGS/lyte")
    if isinstance(deployed, dict) and deployed.get("source_revision"):
        candidates["publisher_dispatch"] = str(deployed["source_revision"]).strip().lower()
    resumed = str(receipt.source_revisions.get("resumed_release") or "").strip().lower()
    if resumed:
        candidates["resumed_receipt"] = resumed
    invalid = sorted(name for name, value in candidates.items() if not SHA.fullmatch(value))
    if invalid:
        receipt.failures.append("release revision candidates are invalid: " + ", ".join(invalid))
        return None
    distinct = sorted(set(candidates.values()))
    if not distinct:
        receipt.failures.append(
            "release requires --source-revision or a verified merged-PR/resume receipt revision"
        )
        return None
    if len(distinct) != 1:
        receipt.failures.append("release revision candidates disagree")
        receipt.checks["release-revision"] = {"candidates": candidates, "closed": False}
        return None
    revision = distinct[0]
    upstream = remote_main()
    closed = upstream == revision
    receipt.checks["release-revision"] = {
        "candidates": candidates,
        "remote_main": upstream,
        "selected": revision,
        "closed": closed,
    }
    if not closed:
        receipt.failures.append("release revision is not the exact current remote main")
        return None
    receipt.source_revisions["release"] = revision
    return revision


def digest_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def artifact_digests() -> dict[str, str]:
    paths = [
        ROOT / ".github" / "workflows" / "compiler.yml",
        ROOT / "Dockerfile",
        ROOT / "docker-compose.yml",
        ROOT / "pyproject.toml",
        ROOT / "requirements.txt",
        ROOT / "SZL_ESTATE_BINDING.json",
        ROOT / "source_revision.txt",
        ROOT / "lyte" / "app.py",
        ROOT / "lyte" / "ui" / "index.html",
        ROOT / "lyte" / "ui" / "styles.css",
        ROOT / "lyte" / "ui" / "app.js",
    ]
    return {
        path.relative_to(ROOT).as_posix(): digest_file(path) for path in paths if path.is_file()
    }


def audit(receipt: RunReceipt, args: argparse.Namespace) -> bool:
    status = git("status", "--porcelain=v1")
    head = current_head()
    branch = current_branch()
    upstream = remote_main()
    receipt.repositories[REPOSITORY] = {
        "available": True,
        "clean": status.passed and not status.stdout.strip(),
        "remote_main": upstream,
    }
    receipt.source_revisions[REPOSITORY] = head
    receipt.branches[REPOSITORY] = branch
    receipt.checks["git-status"] = status.to_dict()
    receipt.checks["github-cli"] = {
        "available": command_available("gh"),
        "authenticated": command_available("gh")
        and execute(["gh", "auth", "status"], quiet=True).passed,
    }
    receipt.checks["huggingface-cli"] = {
        "available": command_available("hf"),
        "authenticated": command_available("hf")
        and execute(["hf", "auth", "whoami", "--format", "json"], quiet=True).passed,
        "required_for_publication": False,
        "publication_authority": "protected-a11oy-workflow",
    }
    if not head or not upstream:
        receipt.failures.append("exact local or remote source revision is unavailable")
    if args.read_only and not status.passed:
        receipt.failures.append("read-only audit could not inspect git status")
    if args.all_verticals:
        receipt.repositories["vertical_rollout"] = {
            "state": "AUDIT_ONLY",
            "lyte_first_gate": "REQUIRED",
            "products": [
                "a11oy",
                "terra",
                "puriq",
                "sentra-aegis-immune",
                "killinchu",
                "counsel",
                "david-leads",
            ],
        }
    return not receipt.failures


def plan(receipt: RunReceipt, args: argparse.Namespace) -> bool:
    del args
    receipt.checks["plan"] = {
        "steps": [
            "audit exact source and conflicts",
            "compile, lint, migrate, and test",
            "build and smoke-test non-root container",
            "open protected pull request",
            "merge only after required exact-head gates",
            "dispatch canonical a11oy single writer",
            "verify exact HF revision and full live contract",
        ]
    }
    return True


def build(receipt: RunReceipt, args: argparse.Namespace) -> bool:
    python = sys.executable
    results = {
        "python-compile": execute(
            [
                python,
                "-I",
                "-B",
                "-m",
                "compileall",
                "-q",
                "lyte",
                "lyte_engine",
                "lyte_api",
                "tools",
            ]
        ),
        "pip-check": execute([python, "-m", "pip", "check"]),
    }
    ok = all(checked(receipt, name, result) for name, result in results.items())
    if args.read_only or args.dry_run:
        receipt.containers["lyte"] = {"state": "SKIPPED_READ_ONLY"}
        return ok
    if not command_available("docker"):
        receipt.failures.append("docker is unavailable")
        return False
    revision = current_head()
    status = git("status", "--porcelain=v1")
    if not revision or not status.passed or status.stdout.strip():
        receipt.failures.append(
            "source-bound container build requires a clean tree at an exact Git revision"
        )
        return False
    result = execute(
        [
            "docker",
            "build",
            "--build-arg",
            f"LYTE_SOURCE_REVISION={revision}",
            "--tag",
            f"szl-lyte:{revision}",
            ".",
        ],
        timeout=1_800,
    )
    receipt.containers["lyte"] = result.to_dict()
    if not result.passed:
        receipt.failures.append("container build failed")
        ok = False
    return ok


def test(receipt: RunReceipt, args: argparse.Namespace) -> bool:
    del args
    python = sys.executable
    env = {
        "LYTE_ENV": "test",
        "DATABASE_URL": "sqlite+pysqlite:///:memory:",
        "LYTE_DEMO_MODE": "true",
    }
    commands = {
        "ruff": [python, "-m", "ruff", "check", "lyte", "tests", "tools"],
        "pytest": [python, "-m", "pytest", "-q"],
        "security": [python, "tools/security_gate.py"],
        "secret-scan": [python, "tools/secret_scan.py"],
        "frontend": [python, "tools/verify_frontend.py", "--mode", "all"],
    }
    ok = True
    for name, argv in commands.items():
        result = execute(argv, env=env)
        ok = checked(receipt, name, result) and ok
    return ok


def ensure_protected_main(receipt: RunReceipt) -> bool:
    result = execute(["gh", "api", f"repos/{REPOSITORY}/branches/main/protection", "--silent"])
    receipt.checks["protected-main"] = result.to_dict()
    if not result.passed:
        receipt.failures.append("main branch protection is not observed; merge refused")
    return result.passed


def pr(receipt: RunReceipt, args: argparse.Namespace) -> bool:
    if args.read_only or args.dry_run:
        receipt.pull_requests[REPOSITORY] = {"state": "SKIPPED_READ_ONLY"}
        return True
    branch = current_branch()
    if not branch or branch == "main":
        receipt.failures.append("PR requires a non-main branch")
        return False
    status = git("status", "--porcelain=v1")
    if not status.passed or status.stdout.strip():
        receipt.failures.append("PR requires a clean working tree")
        return False
    upstream = remote_main()
    if not upstream:
        receipt.failures.append("remote main is unavailable")
        return False
    if not checked(receipt, "fetch-main", git("fetch", "origin", "main")):
        return False
    if not checked(
        receipt, "branch-from-current-main", git("merge-base", "--is-ancestor", upstream, "HEAD")
    ):
        return False
    if not checked(receipt, "push-branch", git("push", "--set-upstream", "origin", branch)):
        return False
    existing = execute(
        [
            "gh",
            "pr",
            "view",
            branch,
            "--repo",
            REPOSITORY,
            "--json",
            "number,url,headRefOid,state,mergedAt,mergeCommit",
        ]
    )
    if existing.passed:
        payload = json.loads(existing.stdout)
    else:
        created = execute(
            [
                "gh",
                "pr",
                "create",
                "--repo",
                REPOSITORY,
                "--base",
                "main",
                "--head",
                branch,
                "--title",
                "feat(lyte): operationalize enterprise Signal Lattice",
                "--body-file",
                str(ROOT / ".github" / "PULL_REQUEST_BODY.md"),
            ]
        )
        if not checked(receipt, "create-pr", created):
            return False
        viewed = execute(
            [
                "gh",
                "pr",
                "view",
                branch,
                "--repo",
                REPOSITORY,
                "--json",
                "number,url,headRefOid,state,mergedAt,mergeCommit",
            ]
        )
        if not checked(receipt, "view-pr", viewed):
            return False
        payload = json.loads(viewed.stdout)
    expected_head = current_head()
    if (
        not expected_head
        or payload.get("state") != "OPEN"
        or str(payload.get("headRefOid", "")).lower() != expected_head
        or remote_branch(branch) != expected_head
    ):
        receipt.failures.append("pull request head is not the exact pushed local revision")
        receipt.pull_requests[REPOSITORY] = payload
        return False
    receipt.pull_requests[REPOSITORY] = payload
    if args.no_merge:
        return True
    if not ensure_protected_main(receipt):
        return False
    number = str(payload["number"])
    if not checked(
        receipt,
        "required-checks",
        execute(
            ["gh", "pr", "checks", number, "--repo", REPOSITORY, "--required", "--watch"],
            timeout=1_800,
        ),
    ):
        return False
    premerge = execute(
        [
            "gh",
            "pr",
            "view",
            number,
            "--repo",
            REPOSITORY,
            "--json",
            "number,url,headRefOid,state,mergedAt,mergeCommit",
        ]
    )
    if not checked(receipt, "premerge-pr-head", premerge):
        return False
    premerge_payload = json.loads(premerge.stdout)
    if (
        premerge_payload.get("state") != "OPEN"
        or str(premerge_payload.get("headRefOid", "")).lower() != expected_head
    ):
        receipt.failures.append("pull request head changed after required checks")
        return False
    if not checked(
        receipt,
        "merge-pr",
        execute(
            [
                "gh",
                "pr",
                "merge",
                number,
                "--repo",
                REPOSITORY,
                "--squash",
                "--delete-branch=false",
                "--match-head-commit",
                expected_head,
            ]
        ),
    ):
        return False
    merged_view = execute(
        [
            "gh",
            "pr",
            "view",
            number,
            "--repo",
            REPOSITORY,
            "--json",
            "number,url,headRefOid,state,mergedAt,mergeCommit",
        ]
    )
    if not checked(receipt, "merged-pr", merged_view):
        return False
    merged_payload = json.loads(merged_view.stdout)
    merged_revision = pull_request_merge_revision(merged_payload)
    if (
        merged_payload.get("state") != "MERGED"
        or not merged_payload.get("mergedAt")
        or not merged_revision
    ):
        receipt.failures.append("GitHub did not report an exact merged pull-request revision")
        return False
    receipt.pull_requests[REPOSITORY] = merged_payload
    for _ in range(10):
        if remote_main() == merged_revision:
            receipt.source_revisions["release"] = merged_revision
            return True
        time.sleep(1)
    receipt.failures.append("merged revision did not become the exact remote main")
    return False


def deploy(receipt: RunReceipt, args: argparse.Namespace) -> bool:
    if args.no_deploy or args.read_only or args.dry_run:
        receipt.deployments["SZLHOLDINGS/lyte"] = {"state": "SKIPPED"}
        return True
    revision = resolve_release_revision(receipt, args)
    if revision is None:
        return False
    command = [
        "gh",
        "workflow",
        "run",
        PUBLISHER_WORKFLOW,
        "--repo",
        PUBLISHER_REPOSITORY,
        "--ref",
        "main",
        "--field",
        f"lyte_source_repository={REPOSITORY}",
        "--field",
        f"lyte_source_revision={revision}",
    ]
    result = execute(command)
    receipt.deployments["SZLHOLDINGS/lyte"] = {
        "publisher": PUBLISHER_REPOSITORY,
        "workflow": PUBLISHER_WORKFLOW,
        "source_revision": revision,
        **result.to_dict(),
    }
    if not result.passed:
        receipt.failures.append("canonical publisher dispatch failed")
    return result.passed


def verify(receipt: RunReceipt, args: argparse.Namespace) -> bool:
    revision = resolve_release_revision(receipt, args)
    if revision is None:
        return False
    result = execute(
        [
            sys.executable,
            "tools/probe_live.py",
            "--base-url",
            args.base_url,
            "--revision",
            revision,
            "--retries",
            str(max(1, args.max_retries)),
        ],
        timeout=max(300, args.max_retries * 240),
    )
    receipt.checks["live-probe"] = result.to_dict()
    if result.stdout:
        try:
            receipt.live_routes = json.loads(result.stdout)
        except json.JSONDecodeError:
            receipt.live_routes = {"state": "INVALID_PROBE_OUTPUT"}
    if not result.passed:
        receipt.failures.append("live route closure failed")
    return result.passed


def rollout(receipt: RunReceipt, args: argparse.Namespace) -> bool:
    if not args.all_verticals:
        receipt.failures.append("rollout requires --all-verticals")
        return False
    receipt.deployments["vertical_rollout"] = {
        "state": "BLOCKED",
        "reason": "Lyte must be merged, deployed, and live-verified before broad rollout",
        "delete_operations": 0,
    }
    receipt.failures.append("vertical rollout is gated on Lyte live verification")
    return False


def finish(receipt: RunReceipt, success: bool, emit_json: bool) -> int:
    receipt.completed_at = now()
    receipt.artifact_sha256 = artifact_digests()
    receipt.complete = success and not receipt.failures
    payload = receipt.to_dict()
    RUNS.mkdir(parents=True, exist_ok=True)
    stamp = receipt.started_at.replace(":", "").replace("-", "").replace(".", "")
    path = RUNS / f"{stamp}-{receipt.command}-{receipt.product}.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if emit_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(
            f"{receipt.command}: {'PASS' if receipt.complete else 'FAILED'} "
            f"receipt={path.relative_to(ROOT).as_posix()}"
        )
        for failure in receipt.failures:
            print(f"- {failure}")
    return 0 if receipt.complete else 1


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    for name in ("audit", "plan", "build", "test", "pr", "deploy", "verify", "all", "rollout"):
        sub = commands.add_parser(name)
        sub.add_argument("--product", default="lyte", choices=("lyte",))
        sub.add_argument("--all-verticals", action="store_true")
        sub.add_argument("--dry-run", action="store_true")
        sub.add_argument("--read-only", action="store_true")
        sub.add_argument("--no-merge", action="store_true")
        sub.add_argument("--no-deploy", action="store_true")
        sub.add_argument("--resume-from", type=Path)
        sub.add_argument("--max-retries", type=int, default=3)
        sub.add_argument("--source-revision")
        sub.add_argument("--base-url", default="https://szlholdings-lyte.hf.space")
        sub.add_argument("--json", action="store_true")
    return root


def main() -> int:
    args = parser().parse_args()
    receipt = RunReceipt(product=args.product, command=args.command)
    if args.resume_from:
        if not args.resume_from.is_file():
            receipt.failures.append("resume receipt does not exist")
            return finish(receipt, False, args.json)
        try:
            previous = json.loads(args.resume_from.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            receipt.failures.append(f"resume receipt is unreadable: {type(exc).__name__}")
            return finish(receipt, False, args.json)
        if not isinstance(previous, dict):
            receipt.failures.append("resume receipt must contain a JSON object")
            return finish(receipt, False, args.json)
        if previous.get("schema") != SCHEMA or previous.get("product") != args.product:
            receipt.failures.append("resume receipt has incompatible schema or product")
            return finish(receipt, False, args.json)
        if (
            previous.get("secret_values_recorded") is not False
            or previous.get("effectors_enabled") is not False
        ):
            receipt.failures.append("resume receipt violates secret or effector invariants")
            return finish(receipt, False, args.json)
        try:
            resumed_revision = resume_release_revision(previous)
        except ValueError as exc:
            receipt.failures.append(str(exc))
            return finish(receipt, False, args.json)
        if resumed_revision:
            receipt.source_revisions["resumed_release"] = resumed_revision
        receipt.checks["resumed_from"] = {
            "path": str(args.resume_from),
            "sha256": digest_file(args.resume_from),
            "previous_command": previous.get("command"),
            "previous_complete": previous.get("complete") is True,
            "release_revision_available": resumed_revision is not None,
        }

    handlers = {
        "audit": audit,
        "plan": plan,
        "build": build,
        "test": test,
        "pr": pr,
        "deploy": deploy,
        "verify": verify,
        "rollout": rollout,
    }
    if args.command == "all":
        success = audit(receipt, args) and build(receipt, args) and test(receipt, args)
        if success and not (args.read_only or args.dry_run):
            success = pr(receipt, args)
        if success and not args.no_deploy and not args.no_merge:
            success = deploy(receipt, args) and verify(receipt, args)
    else:
        success = handlers[args.command](receipt, args)
    return finish(receipt, success, args.json)


if __name__ == "__main__":
    raise SystemExit(main())
