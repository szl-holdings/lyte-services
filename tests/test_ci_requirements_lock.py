"""Hash-locked dependency closures used by CI and the container image.

requirements.txt and requirements-test.txt carry the top-level pins (they mirror
pyproject.toml, which tools/verify_release_scaffolding.py checks). CI and the
Dockerfile install from requirements/ci/runtime.txt and requirements/ci/test.txt
instead: complete closures compiled by ``uv pip compile --universal
--generate-hashes`` and installed with ``pip install --require-hashes``. This
contract fails when a lock drifts from the top-level pins (for example after a
Dependabot bump of requirements.txt), when a lock entry is unpinned or unhashed,
or when CI or the image installs anything outside the hashed closures.
Standard library and pytest only; executes no repository code.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_LOCK = "requirements/ci/runtime.txt"
TEST_LOCK = "requirements/ci/test.txt"
_TOP_LEVEL = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?==([^\s;]+)$")
_LOCKED = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;\\]+)\s*(?:;\s*([^\\]+?))?\s*\\?$")
_HASH = re.compile(r"^\s+--hash=sha256:[0-9a-f]{64}(?:\s*\\)?$")
_PIP_INSTALL = re.compile(r"python -m pip install\b([^\n&;|]*)")


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def top_level_pins(text: str) -> dict[str, str]:
    """{normalized name: version} for one top-level requirements file."""
    pins: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("-r "):
            continue
        match = _TOP_LEVEL.fullmatch(line)
        if match is None:
            raise ValueError(f"top-level requirement is not an exact pin: {line!r}")
        pins[_norm(match.group(1))] = match.group(2)
    return pins


def locked_entries(text: str) -> dict[str, tuple[str, str, int]]:
    """{normalized name: (version, marker, hash count)}; every entry must be hashed."""
    entries: dict[str, tuple[str, str, int]] = {}
    current: str | None = None
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if _HASH.fullmatch(raw):
            if current is None:
                raise ValueError("hash line without a requirement")
            version, marker, count = entries[current]
            entries[current] = (version, marker, count + 1)
            continue
        match = _LOCKED.fullmatch(raw.rstrip())
        if match is None:
            raise ValueError(f"lock line is not an exact hashed pin: {raw!r}")
        current = _norm(match.group(1))
        if current in entries:
            raise ValueError(f"duplicate lock entry: {current}")
        entries[current] = (match.group(2), (match.group(3) or "").strip(), 0)
    unhashed = sorted(name for name, (_, _, count) in entries.items() if count == 0)
    if unhashed:
        raise ValueError(f"lock entries without hashes: {unhashed}")
    return entries


def drift(pins: dict[str, str], lock: dict[str, tuple[str, str, int]]) -> list[str]:
    problems = []
    for name, version in sorted(pins.items()):
        if name not in lock:
            problems.append(f"{name}=={version} is missing from the lock")
        elif lock[name][0] != version:
            problems.append(f"{name}=={version} is locked at {lock[name][0]}")
    return problems


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_runtime_lock_matches_requirements_txt() -> None:
    pins = top_level_pins(_read("requirements.txt"))
    assert drift(pins, locked_entries(_read(RUNTIME_LOCK))) == []


def test_test_lock_matches_runtime_and_test_requirements() -> None:
    pins = {**top_level_pins(_read("requirements.txt")),
            **top_level_pins(_read("requirements-test.txt"))}
    assert drift(pins, locked_entries(_read(TEST_LOCK))) == []


def test_runtime_closure_is_identical_inside_the_test_closure() -> None:
    # One runtime closure: the image and the test jobs must resolve shared
    # packages to the same versions and markers.
    runtime = locked_entries(_read(RUNTIME_LOCK))
    test = locked_entries(_read(TEST_LOCK))
    assert {name: entry[:2] for name, entry in runtime.items()} == {
        name: test[name][:2] for name in runtime if name in test
    }
    assert set(runtime) <= set(test)


def test_ci_installs_only_hashed_closures_and_the_package_without_deps() -> None:
    workflow = _read(".github/workflows/compiler.yml")
    installs = [args.split() for args in _PIP_INSTALL.findall(workflow)]
    assert installs, "compiler.yml installs nothing"
    allowed = (
        ["--require-hashes", "--only-binary=:all:", "--requirement", TEST_LOCK],
        ["--require-hashes", "--only-binary=:all:", "--requirement", RUNTIME_LOCK],
        ["--no-deps", "--editable", "."],
    )
    assert [args for args in installs if args not in allowed] == []
    assert workflow.count("--no-deps --editable .") == workflow.count("--require-hashes")
    assert "requirements-test.txt --editable" not in workflow
    assert "--requirement requirements.txt" not in workflow


def test_container_installs_the_hashed_runtime_closure() -> None:
    dockerfile = _read("Dockerfile")
    assert f"COPY {RUNTIME_LOCK} " in dockerfile
    assert "--require-hashes" in dockerfile and "--only-binary=:all:" in dockerfile
    assert "--requirement requirements.txt" not in dockerfile


def test_dependabot_keeps_the_hashed_closures_current() -> None:
    dependabot = _read(".github/dependabot.yml")
    assert re.search(r'directory:\s*"/requirements/ci"', dependabot)


def test_negative_control_drift_and_unhashed_entries_fail() -> None:
    lock = locked_entries(
        "fastapi==0.162.0 \\\n"
        "    --hash=sha256:" + "a" * 64 + "\n"
        "uvloop==0.23.0 ; sys_platform != 'win32' \\\n"
        "    --hash=sha256:" + "b" * 64 + " \\\n"
        "    --hash=sha256:" + "c" * 64 + "\n"
    )
    assert lock == {
        "fastapi": ("0.162.0", "", 1),
        "uvloop": ("0.23.0", "sys_platform != 'win32'", 2),
    }
    assert drift({"fastapi": "0.163.0"}, lock) == ["fastapi==0.163.0 is locked at 0.162.0"]
    assert drift({"httpx": "0.28.1"}, lock) == ["httpx==0.28.1 is missing from the lock"]
    with pytest.raises(ValueError, match="without hashes"):
        locked_entries("fastapi==0.162.0\n")
    with pytest.raises(ValueError, match="not an exact hashed pin"):
        locked_entries("fastapi>=0.162 \\\n    --hash=sha256:" + "a" * 64 + "\n")
    with pytest.raises(ValueError, match="not an exact pin"):
        top_level_pins("fastapi>=0.162\n")
    assert top_level_pins("-r requirements.txt\nPyJWT[crypto]==2.15.1\n") == {"pyjwt": "2.15.1"}
