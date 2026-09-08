#!/usr/bin/env python3
"""Self-contained tracked-source secret-pattern gate.

This deliberately detects recognizable credential values, not variable names
such as ``HF_TOKEN``. It never prints a matching value.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = (
    re.compile(rb"gh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(rb"github_pat_[A-Za-z0-9_]{40,}"),
    re.compile(rb"hf_[A-Za-z0-9]{24,}"),
    re.compile(rb"sk-(?:live|test|proj)-[A-Za-z0-9_-]{20,}"),
    re.compile(rb"AKIA[0-9A-Z]{16}"),
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)
MAX_FILE_BYTES = 5_000_000
GIT = shutil.which("git")


def tracked_files() -> list[Path]:
    if GIT is None:
        raise RuntimeError("git executable is unavailable")
    result = subprocess.run(  # noqa: S603
        [GIT, "ls-files", "-co", "--exclude-standard"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return [ROOT / line for line in result.stdout.splitlines() if line]


def main() -> int:
    findings: list[str] = []
    scanned = 0
    for path in tracked_files():
        if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            continue
        data = path.read_bytes()
        scanned += 1
        for index, pattern in enumerate(PATTERNS, start=1):
            if pattern.search(data):
                findings.append(f"{path.relative_to(ROOT).as_posix()}:pattern-{index}")
    if findings:
        print("secret-scan: FAILED (values redacted)")
        for finding in findings:
            print(f"- {finding}")
        return 1
    print(f"secret-scan: PASS files={scanned} secret_values_recorded=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
