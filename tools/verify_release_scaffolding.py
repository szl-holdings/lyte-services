#!/usr/bin/env python3
"""Statically verify immutable CI and fail-closed release scaffolding."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIRECTORY = ROOT / ".github" / "workflows"
ACTION_REFERENCE = re.compile(r"^\s*-\s+uses:\s*([^\s]+)\s*$", re.MULTILINE)
IMMUTABLE_ACTION = re.compile(
    r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_./-]+)?@[0-9a-f]{40}$"
)
SHA = re.compile(r"^[0-9a-f]{40}$")


def _dependency_sets() -> tuple[set[str], set[str]]:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    declared_runtime = set(project["project"]["dependencies"])
    declared_test = set(project["project"]["optional-dependencies"]["test"])
    requirements = {
        line.strip()
        for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    test_requirements = {
        line.strip()
        for line in (ROOT / "requirements-test.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith(("#", "-r "))
    }
    return declared_runtime ^ requirements, declared_test ^ test_requirements


def verify() -> list[str]:
    failures: list[str] = []
    workflow_paths = sorted((*WORKFLOW_DIRECTORY.glob("*.yml"), *WORKFLOW_DIRECTORY.glob("*.yaml")))
    if not workflow_paths:
        failures.append("no GitHub Actions workflows were found")
    for path in workflow_paths:
        text = path.read_text(encoding="utf-8")
        if "\t" in text:
            failures.append(f"{path.name}: tab indentation is forbidden")
        references = ACTION_REFERENCE.findall(text)
        for reference in references:
            if reference.startswith("./"):
                continue
            if IMMUTABLE_ACTION.fullmatch(reference) is None:
                failures.append(f"{path.name}: mutable or malformed action reference: {reference}")
        checkout_count = sum(reference.startswith("actions/checkout@") for reference in references)
        if text.count("persist-credentials: false") != checkout_count:
            failures.append(f"{path.name}: every checkout must disable credential persistence")
        for line in text.splitlines():
            if "run:" in line and "${{ inputs." in line:
                failures.append(
                    f"{path.name}: workflow input is interpolated directly into a shell command"
                )

    compiler = (WORKFLOW_DIRECTORY / "compiler.yml").read_text(encoding="utf-8")
    required_jobs = {
        "python-compile",
        "lint",
        "unit",
        "api-contract",
        "release-gates",
        "database-migrations",
        "connector-contract",
        "truth-and-governance",
        "security-scan",
        "secret-scan",
        "frontend-static-contract",
        "accessibility",
        "responsive-overflow",
        "bundle-budget",
        "container-build",
        "container-smoke",
        "source-binding",
        "live-probe",
    }
    job_ids = set(re.findall(r"^  ([a-z][a-z0-9-]+):\s*$", compiler, re.MULTILINE))
    missing_jobs = sorted(required_jobs - job_ids)
    if missing_jobs:
        failures.append("compiler.yml: required jobs missing: " + ", ".join(missing_jobs))
    if "expected_revision:" not in compiler or "required: true" not in compiler:
        failures.append("compiler.yml: manual live proof must require an expected revision")

    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    if re.search(r"^USER\s+10001:10001\s*$", dockerfile, re.MULTILINE) is None:
        failures.append("Dockerfile: numeric non-root runtime user is missing")
    if "ARG LYTE_SOURCE_REVISION=UNAVAILABLE" not in dockerfile:
        failures.append("Dockerfile: exact source-revision build argument is missing")
    if "exact source revision is required" not in dockerfile:
        failures.append("Dockerfile: build does not reject an unavailable source revision")
    if "COPY migrations ./migrations" not in dockerfile or "COPY alembic.ini" not in dockerfile:
        failures.append("Dockerfile: migration closure is incomplete")

    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    obsolete_names = {
        "LYTE_DATABASE_URL",
        "LYTE_ENVIRONMENT",
        "LYTE_DEV_AUTH_MODE",
        "LYTE_OIDC_ISSUER",
        "LYTE_OIDC_AUDIENCE",
        "LYTE_OIDC_JWKS_URL",
    }
    for name in sorted(obsolete_names):
        if re.search(rf"^\s+{name}:", compose, re.MULTILINE):
            failures.append(f"docker-compose.yml: obsolete runtime setting {name}")
    for name in (
        "DATABASE_URL",
        "LYTE_ENV",
        "LYTE_DEV_AUTH_ENABLED",
        "LYTE_SOURCE_REVISION",
        "OIDC_ISSUER",
        "OIDC_AUDIENCE",
        "OIDC_JWKS_URL",
    ):
        if re.search(rf"^\s+{name}:", compose, re.MULTILINE) is None:
            failures.append(f"docker-compose.yml: runtime setting {name} is missing")
    for contract in ("service_completed_successfully", 'cap_drop: ["ALL"]', "read_only: true"):
        if contract not in compose:
            failures.append(
                f"docker-compose.yml: hardening or migration contract missing: {contract}"
            )

    runtime_delta, test_delta = _dependency_sets()
    if runtime_delta:
        failures.append(
            "runtime dependency declarations disagree: " + ", ".join(sorted(runtime_delta))
        )
    if test_delta:
        failures.append("test dependency declarations disagree: " + ", ".join(sorted(test_delta)))

    marker = (ROOT / "source_revision.txt").read_text(encoding="utf-8").strip().lower()
    if marker != "unavailable" and SHA.fullmatch(marker) is None:
        failures.append("source_revision.txt must contain UNAVAILABLE or one exact lowercase SHA")
    return failures


def main() -> int:
    failures = verify()
    if failures:
        print("release-scaffolding: FAILED")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("release-scaffolding: PASS immutable_actions=true exact_revision=true non_root=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
