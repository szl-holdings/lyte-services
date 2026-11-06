#!/usr/bin/env python3
"""Fail-closed gate for the forbidden legacy lookalike domain.

Estate rule (the szl-holdings ``forbidden-domain`` gate): a text line matching
``(?<!-)a11oy\\.com`` is a violation unless the same line is a prohibition or
guard context (it also says never, forbidden, not in, assertNotIn, does not
appear, is not a surface, or blocklist). Canonical surfaces are a-11-oy.com
(product) and a11oy.net (proof).

Tracked and untracked-but-not-ignored files are scanned, as tools.secret_scan
does. A git failure or an empty file listing fails the gate instead of passing
an empty scan. Standard library only.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = re.compile(r"(?<!-)a11oy\.com")
GUARD = re.compile(
    r"never|forbidden|not in|assertNotIn|does not appear|is not a surface|blocklist",
    re.IGNORECASE,
)
MAX_FILE_BYTES = 5_000_000


@dataclass
class ScanResult:
    scanned: int = 0
    guarded: int = 0
    violations: list[str] = field(default_factory=list)


def listed_files(root: Path) -> list[str]:
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("git executable is unavailable")
    result = subprocess.run(  # noqa: S603 - fixed argv, no external input
        [git, "ls-files", "-z", "-co", "--exclude-standard"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return [name for name in result.stdout.decode("utf-8", "surrogateescape").split("\0") if name]


def scan_text(name: str, text: str, result: ScanResult) -> None:
    for number, line in enumerate(text.splitlines(), start=1):
        if not FORBIDDEN.search(line):
            continue
        if GUARD.search(line):
            result.guarded += 1
        else:
            result.violations.append(f"{name}:{number}: {line.strip()[:200]}")


def scan(root: Path, names: list[str]) -> ScanResult:
    result = ScanResult()
    for name in names:
        path = root / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            continue  # symlinks, deleted paths and gitlinks carry no file content here
        data = path.read_bytes()
        if b"\0" in data:
            continue  # binary file
        result.scanned += 1
        scan_text(name, data.decode("utf-8", "replace"), result)
    return result


def main(root: Path = ROOT) -> int:
    try:
        names = listed_files(root)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"forbidden-domain: FAILED could not list repository files ({type(exc).__name__})")
        return 1
    if not names:
        print("forbidden-domain: FAILED git listed no files; refusing to pass an empty scan")
        return 1
    result = scan(root, names)
    if result.violations:
        print("forbidden-domain: FAILED replace with a-11-oy.com or remove; a guard context")
        print("must say so on the same line")
        for violation in result.violations:
            print(f"- {violation}")
        return 1
    print(f"forbidden-domain: PASS files={result.scanned} guarded_references={result.guarded}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
