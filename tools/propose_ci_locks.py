"""Generate a review-only lock patch using a fixed, trusted dependency plan.

Never evaluates headers, imports candidate code, pushes, or edits the candidate.
Run this helper from the trusted base checkout, not the pull-request checkout.
"""

from __future__ import annotations

import argparse
import datetime
import difflib
import hashlib
import json
import os
import re
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

PLAN = (("runtime", None), ("test", "runtime"), ("granite-adapter", "test"))
INPUTS = frozenset(
    {
        "requirements.txt",
        "requirements-test.txt",
        "requirements/ci/runtime.in",
        "requirements/ci/test.in",
        "requirements/ci/granite-adapter.in",
    }
)
PIN = re.compile(
    r"([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[A-Za-z0-9_,.-]+\])?==([A-Za-z0-9][A-Za-z0-9.!+_-]*)"
)
SHA = re.compile(r"[0-9a-f]{40}")
MAX_INPUT_BYTES = 262144
MAX_LOCK_BYTES = 4194304


class ProposalError(RuntimeError):
    """Fixed diagnostic code; never echo provider responses or credentials."""


def read_regular(root: Path, relative: str, limit: int) -> bytes:
    path = root / relative
    for ancestor in [path, *path.parents]:
        if ancestor == root.parent:
            break
        if ancestor.is_symlink():
            raise ProposalError("SYMLINK_INPUT")
    if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise ProposalError("MISSING_OR_ESCAPING_INPUT")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ProposalError("INPUT_TOO_LARGE")
    return data


def exact_pins(root: Path, relative: str, active: tuple[str, ...] = ()) -> list[str]:
    if relative not in INPUTS:
        raise ProposalError("INPUT_NOT_ALLOWLISTED")
    if relative in active:
        raise ProposalError("REQUIREMENT_CYCLE")
    data = read_regular(root, relative, MAX_INPUT_BYTES).decode("utf-8")
    pins: list[str] = []
    for raw in data.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("-r "):
            included = (root / relative).parent / line[3:].strip()
            try:
                normalized = included.resolve().relative_to(root.resolve()).as_posix()
            except ValueError as exc:
                raise ProposalError("ESCAPING_REQUIREMENT") from exc
            pins.extend(exact_pins(root, normalized, (*active, relative)))
        elif PIN.fullmatch(line):
            pins.append(line)
        else:
            raise ProposalError("ONLY_EXACT_INDEX_PINS_ALLOWED")
    versions: dict[str, str] = {}
    for pin in pins:
        match = PIN.fullmatch(pin)
        assert match is not None
        name = re.sub(r"[-_.]+", "-", match[1]).lower()
        if name in versions and versions[name] != match[2]:
            raise ProposalError("CONFLICTING_PINS")
        versions[name] = match[2]
    return sorted(set(pins))


def commands() -> list[list[str]]:
    rows = []
    for name, constraint in PLAN:
        args = [
            "uv",
            "--no-config",
            "pip",
            "compile",
            "--universal",
            "--generate-hashes",
            "--no-build",
            "--python-version",
            "3.12",
        ]
        if constraint:
            args += ["--constraint", constraint + ".txt"]
        rows.append(args + [name + ".in", "--output-file", name + ".txt"])
    return rows


def propose(
    root: Path, output: Path, source_head: str, policy_head: str, runner: Callable = subprocess.run
) -> dict:
    if not SHA.fullmatch(source_head) or not SHA.fullmatch(policy_head):
        raise ProposalError("IMMUTABLE_REVISIONS_REQUIRED")
    root = root.resolve()
    if output.resolve().is_relative_to(root) or root.is_relative_to(output.resolve()):
        raise ProposalError("OUTPUT_MUST_BE_SEPARATE")
    raw_inputs = {name: read_regular(root, name, MAX_INPUT_BYTES) for name in sorted(INPUTS)}
    inputs = {name: exact_pins(root, "requirements/ci/" + name + ".in") for name, _ in PLAN}
    before = {
        name: read_regular(root, "requirements/ci/" + name + ".txt", MAX_LOCK_BYTES)
        for name, _ in PLAN
    }
    output.mkdir(parents=True, exist_ok=False)
    environment = {
        k: v
        for k, v in os.environ.items()
        if k.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME"}
    }
    patches = []
    generated = {}
    with tempfile.TemporaryDirectory(prefix="lyte-lock-proposal-") as scratch:
        scratch_path = Path(scratch)
        for name, pins in inputs.items():
            (scratch_path / (name + ".in")).write_text("\n".join(pins) + "\n", encoding="utf-8")
        for (name, _), argv in zip(PLAN, commands(), strict=True):
            result = runner(
                argv,
                cwd=scratch_path,
                env=environment,
                shell=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=120,
            )
            if result.returncode:
                raise ProposalError("LOCK_COMPILATION_FAILED")
            data = read_regular(scratch_path, name + ".txt", MAX_LOCK_BYTES)
            text = data.decode("utf-8")
            # Compiler output must remain requirements data, not options/URLs.
            seen_requirement = False
            hashes = 0
            for raw in text.splitlines():
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("--hash=sha256:"):
                    if not seen_requirement or not re.fullmatch(
                        r"--hash=sha256:[0-9a-f]{64}\s*\\?", line
                    ):
                        raise ProposalError("INVALID_LOCK_HASH")
                    hashes += 1
                elif re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]*==[^\s/\\:@]+(?:\s|$)", line):
                    if seen_requirement and not hashes:
                        raise ProposalError("UNHASHED_LOCK_REQUIREMENT")
                    seen_requirement = True
                    hashes = 0
                else:
                    raise ProposalError("UNEXPECTED_LOCK_OUTPUT")
            if not seen_requirement or not hashes:
                raise ProposalError("EMPTY_OR_UNHASHED_LOCK")
            relative = "requirements/ci/" + name + ".txt"
            patches.extend(
                difflib.unified_diff(
                    before[name].decode("utf-8").splitlines(True),
                    text.splitlines(True),
                    fromfile="a/" + relative,
                    tofile="b/" + relative,
                )
            )
            generated[relative] = hashlib.sha256(data).hexdigest()
    for name, old in raw_inputs.items():
        if read_regular(root, name, MAX_INPUT_BYTES) != old:
            raise ProposalError("INPUT_CHANGED_DURING_RESOLUTION")
    for name, old in before.items():
        if read_regular(root, "requirements/ci/" + name + ".txt", MAX_LOCK_BYTES) != old:
            raise ProposalError("LOCK_CHANGED_DURING_RESOLUTION")
    patch_bytes = "".join(patches).encode("utf-8")
    (output / "lockfiles.patch").write_bytes(patch_bytes)
    report = {
        "schema": "szl.lyte.lock-proposal/v1",
        "observed_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "source_head": source_head,
        "trusted_policy_head": policy_head,
        "status": "PROPOSED" if patch_bytes else "NO_CHANGE",
        "input_sha256": {k: hashlib.sha256(v).hexdigest() for k, v in raw_inputs.items()},
        "generated_sha256": generated,
        "patch_sha256": hashlib.sha256(patch_bytes).hexdigest(),
        "candidate_writes": 0,
        "repository_pushes": 0,
        "approval_granted": False,
    }
    (output / "proposal.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-head", required=True)
    parser.add_argument("--policy-head", required=True)
    args = parser.parse_args()
    try:
        report = propose(args.root, args.output, args.source_head, args.policy_head)
    except (ProposalError, OSError, UnicodeError, subprocess.SubprocessError) as exc:
        print(
            json.dumps(
                {
                    "status": "HOLD",
                    "reason": str(exc) if isinstance(exc, ProposalError) else type(exc).__name__,
                    "approval_granted": False,
                }
            )
        )
        return 2
    print(json.dumps({"status": report["status"], "approval_granted": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
