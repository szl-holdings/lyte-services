#!/usr/bin/env python3
"""Verify product-source identity against the exact CI revision."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
SHA = re.compile(r"^[0-9a-f]{40}$")
CANONICAL_REPOSITORY_PATH = "/szl-holdings/lyte-services"
CANONICAL_SCP_REMOTE = re.compile(
    r"git@github\.com:szl-holdings/lyte-services(?:\.git)?",
    re.ASCII | re.IGNORECASE,
)
GIT = shutil.which("git")


def is_canonical_remote(value: str) -> bool:
    """Return true only for credential-free canonical GitHub remote URLs."""
    candidate = value.strip()
    if CANONICAL_SCP_REMOTE.fullmatch(candidate):
        return True
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError:
        return False
    if (
        parsed.scheme.lower() != "https"
        or parsed.netloc.lower() != "github.com"
        or (parsed.hostname or "").lower() != "github.com"
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.query
        or parsed.fragment
    ):
        return False
    path = parsed.path.lower()
    return path in {CANONICAL_REPOSITORY_PATH, f"{CANONICAL_REPOSITORY_PATH}.git"}


def git(*args: str) -> subprocess.CompletedProcess[str]:
    if GIT is None:
        raise RuntimeError("git executable is unavailable")
    return subprocess.run(  # noqa: S603
        [GIT, *args],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--revision", required=True)
    args = parser.parse_args()
    expected = args.revision.strip().lower()
    if not SHA.fullmatch(expected):
        print("source-binding: FAILED invalid expected revision")
        return 1
    result = git("rev-parse", "HEAD")
    actual = result.stdout.strip().lower()
    binding = json.loads((ROOT / "SZL_ESTATE_BINDING.json").read_text(encoding="utf-8"))
    failures: list[str] = []
    if result.returncode != 0 or actual != expected:
        failures.append("checked-out HEAD does not equal expected revision")
    if binding.get("organ") != "lyte":
        failures.append("estate organ is not lyte")
    if binding.get("hub_surface") != "SZLHOLDINGS/lyte":
        failures.append("estate hub surface is not canonical")
    if binding.get("schema") != "szl.estate.binding/v1":
        failures.append("estate binding schema is not canonical")
    remote = git("remote", "get-url", "origin")
    if remote.returncode != 0 or not is_canonical_remote(remote.stdout):
        failures.append("origin remote is not the canonical Lyte repository")
    marker = (ROOT / "source_revision.txt").read_text(encoding="utf-8").strip().lower()
    if marker != "unavailable" and (not SHA.fullmatch(marker) or marker != expected):
        failures.append("source revision marker disagrees with the expected revision")
    os.environ["LYTE_SOURCE_REVISION"] = expected
    from lyte.api.routes_health import source_revision

    if source_revision() != expected:
        failures.append("runtime source resolver did not return expected revision")
    if failures:
        print("source-binding: FAILED")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print(f"source-binding: PASS revision={expected} bindings_agree=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
