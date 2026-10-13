"""Bind the SHA-256 tables in docs/native-frontdoor.md to actual source bytes.

"Current accepted source digests" must equal the raw bytes of the three front-door
assets in this checkout. "Historical qualification (2026-10-11)" must equal the
bytes at its declared immutable revision; that binding needs the revision's commit,
which shallow CI checkouts do not fetch, so it is skipped there with a reason.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "native-frontdoor.md"
ASSETS = ("lyte/ui/index.html", "lyte/ui/styles.css", "lyte/ui/app.js")
CURRENT = "Current accepted source digests"
HISTORICAL = "Historical qualification (2026-10-11)"
HISTORICAL_REVISION = "b7bfc276cf9c8578e1e443e7a79c19594e1276de"

HEADING = re.compile(r"#{1,6} (.+?)\s*")
SEPARATOR = re.compile(r"\|(\s*:?-+:?\s*\|)+")
ROW = re.compile(r"\|\s*`([^`]+)`\s*\|\s*`([0-9a-f]{64})`\s*\|")
REVISION = re.compile(r"Qualified revision: `([0-9a-f]{40})`")

Reader = Callable[[str], bytes | None]


def document() -> str:
    return DOC.read_text(encoding="utf-8")


def section(text: str, heading: str) -> list[str] | None:
    """Return the stripped lines under exactly one matching heading, else None."""
    lines = text.splitlines()
    starts = [
        index
        for index, line in enumerate(lines)
        if (match := HEADING.fullmatch(line)) and match.group(1) == heading
    ]
    if len(starts) != 1:
        return None
    body: list[str] = []
    for line in lines[starts[0] + 1 :]:
        if HEADING.fullmatch(line):
            break
        body.append(line.strip())
    return body


def table_rows(body: list[str]) -> tuple[dict[str, str], list[str]]:
    """Parse one path/digest table; report malformed and duplicate rows."""
    table = [line for line in body if line.startswith("|")]
    if len(table) < 2 or not SEPARATOR.fullmatch(table[1]):
        return {}, ["digest table header or separator is missing"]
    rows: dict[str, str] = {}
    problems: list[str] = []
    for line in table[2:]:
        match = ROW.fullmatch(line)
        if match is None:
            problems.append(f"malformed digest row: {line}")
            continue
        path, digest = match.groups()
        if path in rows:
            problems.append(f"duplicate digest row for {path}")
        else:
            rows[path] = digest
    return rows, problems


def digest_failures(text: str, heading: str, read: Reader) -> list[str]:
    body = section(text, heading)
    if body is None:
        return [f"expected exactly one {heading!r} heading"]
    rows, problems = table_rows(body)
    problems += [f"missing digest row for {path}" for path in ASSETS if path not in rows]
    for path, documented in rows.items():
        raw = read(path)
        if raw is None:
            problems.append(f"{path} is listed but unavailable")
            continue
        actual = hashlib.sha256(raw).hexdigest()
        if actual != documented:
            crlf = b"\r\n" in raw
            hint = " (file has CRLF; digests are of the committed LF bytes)" if crlf else ""
            problems.append(f"{path}: documented {documented}, actual {actual}{hint}")
    return problems


def working_tree(root: Path) -> Reader:
    def read(path: str) -> bytes | None:
        candidate = root / path
        return candidate.read_bytes() if candidate.is_file() else None

    return read


def git_revision(revision: str) -> Reader | None:
    """Read raw blobs at an immutable revision, or None when the commit is absent."""
    git = shutil.which("git")
    if git is None:
        return None
    probe = subprocess.run(  # noqa: S603 - fixed git arguments, no shell
        [git, "-C", str(ROOT), "cat-file", "-e", f"{revision}^{{commit}}"],
        capture_output=True,
        check=False,
    )
    if probe.returncode != 0:
        return None

    def read(path: str) -> bytes | None:
        blob = subprocess.run(  # noqa: S603 - fixed git arguments, no shell
            [git, "-C", str(ROOT), "cat-file", "blob", f"{revision}:{path}"],
            capture_output=True,
            check=False,
        )
        return blob.stdout if blob.returncode == 0 else None

    return read


def edit_current_section(text: str, edit: Callable[[str], str]) -> str:
    head, marker, tail = text.partition(f"### {CURRENT}\n")
    assert marker, "current digest heading not found"
    return head + marker + edit(tail)


def current_row(path: str, digest: str | None = None) -> str:
    digest = digest or hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
    return f"| `{path}` | `{digest}` |\n"


@pytest.fixture
def asset_copy(tmp_path: Path) -> Path:
    for path in ASSETS:
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / path).read_bytes())
    return tmp_path


def test_current_digest_table_matches_raw_asset_bytes() -> None:
    assert digest_failures(document(), CURRENT, working_tree(ROOT)) == []


def test_unmodified_asset_copy_passes(asset_copy: Path) -> None:
    assert digest_failures(document(), CURRENT, working_tree(asset_copy)) == []


@pytest.mark.parametrize("path", ASSETS)
def test_one_byte_asset_mutation_fails(asset_copy: Path, path: str) -> None:
    target = asset_copy / path
    mutated = bytearray(target.read_bytes())
    mutated[len(mutated) // 2] ^= 0x01
    target.write_bytes(bytes(mutated))

    failures = digest_failures(document(), CURRENT, working_tree(asset_copy))

    assert len(failures) == 1
    assert failures[0].startswith(f"{path}: documented ")


@pytest.mark.parametrize("path", ASSETS)
def test_missing_current_row_fails(path: str) -> None:
    text = edit_current_section(document(), lambda tail: tail.replace(current_row(path), "", 1))
    assert digest_failures(text, CURRENT, working_tree(ROOT)) == [f"missing digest row for {path}"]


@pytest.mark.parametrize("path", ASSETS)
def test_duplicate_current_row_fails(path: str) -> None:
    row = current_row(path)
    text = edit_current_section(document(), lambda tail: tail.replace(row, row + row, 1))
    assert digest_failures(text, CURRENT, working_tree(ROOT)) == [
        f"duplicate digest row for {path}"
    ]


def test_malformed_current_row_fails() -> None:
    path = ASSETS[0]
    digest = hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
    row, truncated = current_row(path), current_row(path, digest[:-1])
    text = edit_current_section(document(), lambda tail: tail.replace(row, truncated, 1))
    failures = digest_failures(text, CURRENT, working_tree(ROOT))
    assert failures == [
        f"malformed digest row: {truncated.strip()}",
        f"missing digest row for {path}",
    ]


def test_historical_section_declares_its_immutable_revision() -> None:
    body = section(document(), HISTORICAL)
    assert body is not None
    revisions = [match.group(1) for line in body if (match := REVISION.fullmatch(line))]
    assert revisions == [HISTORICAL_REVISION]
    rows, problems = table_rows(body)
    assert problems == []
    assert sorted(rows) == sorted(ASSETS)


def historical_reader() -> Reader:
    read = git_revision(HISTORICAL_REVISION)
    if read is None:
        pytest.skip(
            f"commit {HISTORICAL_REVISION} is not in this checkout (shallow CI checkouts "
            "fetch only the tested revision); `git fetch origin refs/pull/42/head` enables it"
        )
    return read


def test_historical_values_bind_to_the_qualified_revision() -> None:
    assert digest_failures(document(), HISTORICAL, historical_reader()) == []


@pytest.mark.parametrize("path", ASSETS)
def test_historical_binding_rejects_an_altered_value(path: str) -> None:
    read = historical_reader()
    text = document()
    head, marker, tail = text.partition(f"### {HISTORICAL}\n")
    assert marker, "historical digest heading not found"
    body = section(text, HISTORICAL)
    assert body is not None
    documented = table_rows(body)[0][path]
    flipped = documented[:-1] + ("0" if documented[-1] != "0" else "1")
    altered = head + marker + tail.replace(f"`{documented}`", f"`{flipped}`", 1)

    failures = digest_failures(altered, HISTORICAL, read)

    assert len(failures) == 1
    assert failures[0].startswith(f"{path}: documented {flipped}")
