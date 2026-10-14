"""Bind the SHA-256 tables in docs/native-frontdoor.md to actual source bytes.

"Current accepted source digests" must equal the raw bytes of the three front-door
assets in this checkout. "Historical qualification (2026-10-11)" must equal the bytes
at its declared revision, b7bfc276. That commit is outside main's ancestry (only the
closed pull request #42 reaches it), so no checkout of main contains it, shallow or
full. It is preserved in-tree instead, as canonical uncompressed git objects named by
their object ids under ``tests/fixtures/frontdoor_b7bfc276/objects/``: the commit, the
three trees on the path to ``lyte/ui/`` and the three asset blobs.

Every object is re-hashed against its id when it is read, from the commit down to each
blob, so the asset bytes are bound to the commit id rather than to the fixture's word.
``sha1sum`` of each fixture file reproduces its name, and in a clone that has fetched
``refs/pull/42/head`` the bytes after the first NUL equal ``git cat-file <type> <id>``.
A missing or altered object fails the historical cases. Nothing here skips.
"""

from __future__ import annotations

import hashlib
import re
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "native-frontdoor.md"
ASSETS = ("lyte/ui/index.html", "lyte/ui/styles.css", "lyte/ui/app.js")
CURRENT = "Current accepted source digests"
HISTORICAL = "Historical qualification (2026-10-11)"
HISTORICAL_REVISION = "b7bfc276cf9c8578e1e443e7a79c19594e1276de"
HISTORICAL_PARENT = "7e4c1742e9b682b5274fe4e8ba81fd8b79748a02"
PRESERVED = ROOT / "tests" / "fixtures" / "frontdoor_b7bfc276" / "objects"
# The qualified commit and every object on its path to the three assets.
PRESERVED_OBJECTS = {
    HISTORICAL_REVISION: "commit",
    "018ba19b370a4358fd632c1aa8b8f374e3568f70": "tree",  # /
    "dac945e9851ea27e5a9b23af5501ed8243ef8b72": "tree",  # lyte/
    "e9257741258213741ed463592ca1ce8b3d75ca6e": "tree",  # lyte/ui/
    "f50fedc9a4f62c56050cc934c990037995bb7e08": "blob",  # lyte/ui/index.html
    "0d1364234b0f9a30db4ea52e9d992156144d2a81": "blob",  # lyte/ui/styles.css
    "1d8b21c1fa53c52bc8b6addda6d4574e8e344f78": "blob",  # lyte/ui/app.js
}

HEADING = re.compile(r"#{1,6} (.+?)\s*")
SEPARATOR = re.compile(r"\|(\s*:?-+:?\s*\|)+")
ROW = re.compile(r"\|\s*`([^`]+)`\s*\|\s*`([0-9a-f]{64})`\s*\|")
REVISION = re.compile(r"Qualified revision: `([0-9a-f]{40})`")
COMMIT_TREE = re.compile(rb"tree ([0-9a-f]{40})\n")
DIRECTORY = (b"40000",)
REGULAR_FILE = (b"100644", b"100755")

Reader = Callable[[str], bytes | None]


class PreservedRevisionError(AssertionError):
    """The preserved revision is incomplete or does not hash to its object ids."""


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


def load_object(store: Path, oid: str, kind: str) -> bytes:
    """Return the body of one canonical git object after checking its id and type."""
    path = store / oid
    if not path.is_file():
        raise PreservedRevisionError(f"git object {oid} is missing from {store}")
    raw = path.read_bytes()
    actual = hashlib.sha1(raw).hexdigest()  # noqa: S324 - git object ids are SHA-1
    if actual != oid:
        raise PreservedRevisionError(f"git object {oid} hashes to {actual}")
    header, nul, body = raw.partition(b"\0")
    if not nul or header != f"{kind} {len(body)}".encode():
        raise PreservedRevisionError(f"git object {oid} is not a {kind} object")
    return body


def tree_entry(tree: bytes, name: str, modes: tuple[bytes, ...]) -> str | None:
    """Return the object id of ``name`` in a raw tree body if its mode is in ``modes``."""
    while tree:
        entry, nul, rest = tree.partition(b"\0")
        mode, space, entry_name = entry.partition(b" ")
        if not nul or not space or len(rest) < 20:
            raise PreservedRevisionError("malformed tree entry")
        if entry_name == name.encode():
            return rest[:20].hex() if mode in modes else None
        tree = rest[20:]
    return None


def preserved_revision(store: Path, revision: str) -> Reader:
    """Read file bytes at ``revision`` from preserved git objects, verifying every hop."""
    match = COMMIT_TREE.match(load_object(store, revision, "commit"))
    if match is None:
        raise PreservedRevisionError(f"commit {revision} names no tree")
    root = match.group(1).decode()

    def read(path: str) -> bytes | None:
        *directories, name = path.split("/")
        tree = root
        for directory in directories:
            found = tree_entry(load_object(store, tree, "tree"), directory, DIRECTORY)
            if found is None:
                return None
            tree = found
        blob = tree_entry(load_object(store, tree, "tree"), name, REGULAR_FILE)
        return None if blob is None else load_object(store, blob, "blob")

    return read


def historical_reader(store: Path = PRESERVED) -> Reader:
    return preserved_revision(store, HISTORICAL_REVISION)


def one_character_mutation(digest: str) -> str:
    """Change only the last hex digit, so the row keeps a valid 64-digit digest."""
    mutated = digest[:-1] + ("0" if digest[-1] != "0" else "1")
    assert len(mutated) == len(digest) == 64
    assert sum(a != b for a, b in zip(digest, mutated, strict=True)) == 1
    return mutated


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


@pytest.fixture
def preserved_copy(tmp_path: Path) -> Path:
    target = tmp_path / "objects"
    shutil.copytree(PRESERVED, target)
    return target


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
def test_one_character_current_digest_mutation_fails(path: str) -> None:
    actual = hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
    mutated = one_character_mutation(actual)
    text = edit_current_section(
        document(), lambda tail: tail.replace(current_row(path), current_row(path, mutated), 1)
    )
    assert digest_failures(text, CURRENT, working_tree(ROOT)) == [
        f"{path}: documented {mutated}, actual {actual}"
    ]


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


def test_preserved_store_is_exactly_the_qualified_commit_path() -> None:
    assert sorted(path.name for path in PRESERVED.iterdir()) == sorted(PRESERVED_OBJECTS)
    for oid, kind in PRESERVED_OBJECTS.items():
        load_object(PRESERVED, oid, kind)
    commit = load_object(PRESERVED, HISTORICAL_REVISION, "commit").splitlines()
    assert commit[1] == f"parent {HISTORICAL_PARENT}".encode()


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
    flipped = one_character_mutation(documented)
    altered = head + marker + tail.replace(f"`{documented}`", f"`{flipped}`", 1)

    assert digest_failures(altered, HISTORICAL, read) == [
        f"{path}: documented {flipped}, actual {documented}"
    ]


def test_historical_binding_rejects_current_bytes() -> None:
    historical, current = historical_reader(), working_tree(ROOT)
    changed = [path for path in ASSETS if historical(path) != current(path)]
    failures = digest_failures(document(), HISTORICAL, current)
    assert changed, "current bytes equal the qualified bytes; the split tables are stale"
    assert [failure.split(":", 1)[0] for failure in failures] == changed


@pytest.mark.parametrize("oid", sorted(PRESERVED_OBJECTS))
def test_missing_preserved_object_fails_closed(preserved_copy: Path, oid: str) -> None:
    (preserved_copy / oid).unlink()
    with pytest.raises(PreservedRevisionError, match=f"git object {oid} is missing"):
        digest_failures(document(), HISTORICAL, historical_reader(preserved_copy))


@pytest.mark.parametrize("oid", sorted(PRESERVED_OBJECTS))
def test_altered_preserved_object_fails_closed(preserved_copy: Path, oid: str) -> None:
    target = preserved_copy / oid
    raw = bytearray(target.read_bytes())
    raw[-1] ^= 0x01
    target.write_bytes(bytes(raw))
    with pytest.raises(PreservedRevisionError, match=f"git object {oid} hashes to "):
        digest_failures(document(), HISTORICAL, historical_reader(preserved_copy))
