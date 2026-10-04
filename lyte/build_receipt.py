"""Deterministic build-receipt generation and fail-closed runtime verification."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

SCHEMA = "szl.lyte-build-receipt/v1"
SERVICE = "lyte-signal-lattice"
SOURCE_REPOSITORY = "szl-holdings/lyte-services"
PAYLOAD_SCOPE = "lyte-application-files/v1"
ALGORITHM = "sha256"
RECEIPT_NAME = "build-receipt.json"
PAYLOAD_ROOTS = ("lyte", "lyte_engine", "lyte_api", "space", "migrations")
PAYLOAD_FILES = (
    "requirements.txt",
    "alembic.ini",
    "README.md",
    "LICENSE",
    "NOTICE",
    "source_revision.txt",
)
MAX_RECEIPT_BYTES = 4_000_000
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_PATH = re.compile(r"^[A-Za-z0-9._/-]+$")


class BuildReceiptError(RuntimeError):
    """A build receipt cannot be generated or verified."""


@dataclass(frozen=True, slots=True)
class BuildReceiptObservation:
    """Bounded public observation of one stored receipt and its covered payload."""

    state: str
    valid: bool
    required: bool
    receipt_sha256: str | None = None
    payload_sha256: str | None = None
    source_revision: str | None = None
    failures: tuple[str, ...] = ()
    raw: bytes | None = field(default=None, repr=False)
    document: dict[str, Any] | None = field(default=None, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "valid": self.valid,
            "required": self.required,
            "receipt_sha256": self.receipt_sha256,
            "payload_sha256": self.payload_sha256,
            "source_revision": self.source_revision,
            "failures": list(self.failures),
            "truth_label": "MEASURED" if self.valid else "UNAVAILABLE",
        }


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_relative_path(value: str) -> None:
    if not value or _SAFE_PATH.fullmatch(value) is None or "\\" in value:
        raise BuildReceiptError("receipt contains an unsafe payload path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise BuildReceiptError("receipt contains an unsafe payload path")


def _walk_payload_root(application_root: Path, relative_root: str) -> list[Path]:
    root = application_root / relative_root
    if not root.is_dir() or root.is_symlink():
        raise BuildReceiptError(f"required payload root is unavailable: {relative_root}")
    files: list[Path] = []
    for directory, directory_names, file_names in os.walk(root, followlinks=False):
        current = Path(directory)
        kept_directories: list[str] = []
        for name in sorted(directory_names):
            candidate = current / name
            if candidate.is_symlink():
                raise BuildReceiptError("payload symlinks are forbidden")
            if name == "__pycache__":
                continue
            kept_directories.append(name)
        directory_names[:] = kept_directories
        for name in sorted(file_names):
            candidate = current / name
            if candidate.is_symlink():
                raise BuildReceiptError("payload symlinks are forbidden")
            if not candidate.is_file():
                raise BuildReceiptError("payload must contain regular files only")
            files.append(candidate)
    return files


def collect_payload_entries(application_root: Path) -> list[dict[str, Any]]:
    """Enumerate the schema-defined closed application payload."""

    root = application_root.resolve(strict=True)
    paths: list[Path] = []
    for relative_root in PAYLOAD_ROOTS:
        paths.extend(_walk_payload_root(root, relative_root))
    for relative_file in PAYLOAD_FILES:
        candidate = root / relative_file
        if candidate.is_symlink() or not candidate.is_file():
            raise BuildReceiptError(f"required payload file is unavailable: {relative_file}")
        paths.append(candidate)

    entries: list[dict[str, Any]] = []
    for path in paths:
        relative = path.relative_to(root).as_posix()
        _validate_relative_path(relative)
        entries.append(
            {
                "path": relative,
                "size": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
        )
    entries.sort(key=lambda item: item["path"])
    if len(entries) != len({item["path"] for item in entries}):
        raise BuildReceiptError("payload contains duplicate paths")
    return entries


def _manifest_basis(source_revision: str, entries: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "service": SERVICE,
        "source": {
            "repository": SOURCE_REPOSITORY,
            "revision": source_revision,
        },
        "payload": {
            "scope": PAYLOAD_SCOPE,
            "algorithm": ALGORITHM,
            "files": entries,
        },
    }


def _source_revision(application_root: Path, supplied: str | None) -> str:
    marker = (application_root / "source_revision.txt").read_text(encoding="utf-8").strip().lower()
    requested = (supplied or "").strip().lower()
    if _SHA40.fullmatch(marker) is None:
        raise BuildReceiptError("source_revision.txt must contain an exact source revision")
    if requested and _SHA40.fullmatch(requested) is None:
        raise BuildReceiptError("source revision must be 40 lowercase hexadecimal characters")
    if requested and requested != marker:
        raise BuildReceiptError("source revision does not agree with source_revision.txt")
    return requested or marker


def build_receipt_document(
    application_root: Path,
    *,
    source_revision: str | None = None,
) -> dict[str, Any]:
    revision = _source_revision(application_root, source_revision)
    entries = collect_payload_entries(application_root)
    basis = _manifest_basis(revision, entries)
    return {
        **basis,
        "payload": {
            **basis["payload"],
            "sha256": _sha256_bytes(_canonical_json(basis)),
        },
    }


def generate_build_receipt(
    application_root: Path,
    *,
    source_revision: str | None = None,
    output_path: Path | None = None,
) -> BuildReceiptObservation:
    """Generate one canonical receipt, then independently verify the stored bytes."""

    root = application_root.resolve(strict=True)
    output = output_path or root / RECEIPT_NAME
    if output.parent.resolve(strict=True) != root:
        raise BuildReceiptError("build receipt must be stored at the application root")
    if output.is_symlink():
        raise BuildReceiptError("build receipt cannot be a symlink")
    document = build_receipt_document(root, source_revision=source_revision)
    encoded = _canonical_json(document) + b"\n"
    temporary = output.with_name(f".{output.name}.tmp")
    temporary.write_bytes(encoded)
    os.replace(temporary, output)
    observation = observe_build_receipt(
        root,
        expected_source_revision=document["source"]["revision"],
        required=True,
        receipt_path=output,
    )
    if not observation.valid:
        raise BuildReceiptError("generated build receipt did not verify")
    return observation


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BuildReceiptError("receipt contains duplicate JSON object keys")
        result[key] = value
    return result


def _parse_document(raw: bytes) -> dict[str, Any]:
    try:
        document = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except (BuildReceiptError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise BuildReceiptError("receipt is not unambiguous UTF-8 JSON") from exc
    if not isinstance(document, dict):
        raise BuildReceiptError("receipt root must be a JSON object")
    if raw != _canonical_json(document) + b"\n":
        raise BuildReceiptError("receipt bytes are not canonical JSON")
    return document


def _validate_document(document: dict[str, Any]) -> tuple[str, str, list[dict[str, Any]]]:
    if set(document) != {"schema", "service", "source", "payload"}:
        raise BuildReceiptError("receipt top-level fields are invalid")
    if document["schema"] != SCHEMA or document["service"] != SERVICE:
        raise BuildReceiptError("receipt schema or service is unsupported")
    source = document["source"]
    payload = document["payload"]
    if not isinstance(source, dict) or set(source) != {"repository", "revision"}:
        raise BuildReceiptError("receipt source contract is invalid")
    if source["repository"] != SOURCE_REPOSITORY:
        raise BuildReceiptError("receipt source repository is not canonical")
    revision = source["revision"]
    if not isinstance(revision, str) or _SHA40.fullmatch(revision) is None:
        raise BuildReceiptError("receipt source revision is invalid")
    if not isinstance(payload, dict) or set(payload) != {
        "scope",
        "algorithm",
        "files",
        "sha256",
    }:
        raise BuildReceiptError("receipt payload contract is invalid")
    if payload["scope"] != PAYLOAD_SCOPE or payload["algorithm"] != ALGORITHM:
        raise BuildReceiptError("receipt payload scope or algorithm is unsupported")
    payload_sha256 = payload["sha256"]
    if not isinstance(payload_sha256, str) or _SHA256.fullmatch(payload_sha256) is None:
        raise BuildReceiptError("receipt payload digest is invalid")
    files = payload["files"]
    if not isinstance(files, list) or not files:
        raise BuildReceiptError("receipt payload manifest is empty")
    validated: list[dict[str, Any]] = []
    for entry in files:
        if not isinstance(entry, dict) or set(entry) != {"path", "size", "sha256"}:
            raise BuildReceiptError("receipt payload entry is invalid")
        path = entry["path"]
        size = entry["size"]
        digest = entry["sha256"]
        if not isinstance(path, str):
            raise BuildReceiptError("receipt payload path is invalid")
        _validate_relative_path(path)
        if type(size) is not int or size < 0:  # bool is intentionally rejected
            raise BuildReceiptError("receipt payload size is invalid")
        if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
            raise BuildReceiptError("receipt payload file digest is invalid")
        validated.append({"path": path, "size": size, "sha256": digest})
    paths = [entry["path"] for entry in validated]
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise BuildReceiptError("receipt payload paths must be unique and sorted")
    return revision, payload_sha256, validated


def observe_build_receipt(
    application_root: Path,
    *,
    expected_source_revision: str | None,
    required: bool,
    receipt_path: Path | None = None,
) -> BuildReceiptObservation:
    """Measure a stored receipt against fresh source and payload bytes."""

    root = application_root.resolve(strict=True)
    path = receipt_path or root / RECEIPT_NAME
    if not path.exists():
        return BuildReceiptObservation(
            state="MISSING" if required else "MISSING_OPTIONAL_LOCAL",
            valid=False,
            required=required,
            failures=("BUILD_RECEIPT_MISSING",),
        )
    if path.is_symlink() or not path.is_file():
        return BuildReceiptObservation(
            state="INVALID",
            valid=False,
            required=required,
            failures=("BUILD_RECEIPT_NOT_REGULAR_FILE",),
        )
    try:
        raw = path.read_bytes()
        if not raw or len(raw) > MAX_RECEIPT_BYTES:
            raise BuildReceiptError("receipt size is outside the accepted bounds")
        receipt_sha256 = _sha256_bytes(raw)
        document = _parse_document(raw)
        revision, payload_sha256, entries = _validate_document(document)
        if expected_source_revision is None or revision != expected_source_revision:
            return BuildReceiptObservation(
                state="SOURCE_MISMATCH",
                valid=False,
                required=required,
                receipt_sha256=receipt_sha256,
                payload_sha256=payload_sha256,
                source_revision=revision,
                failures=("BUILD_RECEIPT_SOURCE_MISMATCH",),
                raw=raw,
                document=document,
            )
        basis = _manifest_basis(revision, entries)
        if _sha256_bytes(_canonical_json(basis)) != payload_sha256:
            return BuildReceiptObservation(
                state="INVALID",
                valid=False,
                required=required,
                receipt_sha256=receipt_sha256,
                payload_sha256=payload_sha256,
                source_revision=revision,
                failures=("BUILD_RECEIPT_MANIFEST_DIGEST_MISMATCH",),
                raw=raw,
                document=document,
            )
        current_entries = collect_payload_entries(root)
        if current_entries != entries:
            return BuildReceiptObservation(
                state="PAYLOAD_MISMATCH",
                valid=False,
                required=required,
                receipt_sha256=receipt_sha256,
                payload_sha256=payload_sha256,
                source_revision=revision,
                failures=("BUILD_RECEIPT_PAYLOAD_MISMATCH",),
                raw=raw,
                document=document,
            )
    except (BuildReceiptError, OSError, UnicodeError):
        return BuildReceiptObservation(
            state="INVALID",
            valid=False,
            required=required,
            failures=("BUILD_RECEIPT_INVALID",),
        )
    return BuildReceiptObservation(
        state="VERIFIED",
        valid=True,
        required=required,
        receipt_sha256=receipt_sha256,
        payload_sha256=payload_sha256,
        source_revision=revision,
        raw=raw,
        document=document,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("generate", "verify"))
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--source-revision")
    args = parser.parse_args()
    try:
        if args.command == "generate":
            observation = generate_build_receipt(
                args.root,
                source_revision=args.source_revision,
            )
        else:
            observation = observe_build_receipt(
                args.root,
                expected_source_revision=args.source_revision,
                required=True,
            )
    except (BuildReceiptError, OSError) as exc:
        print(json.dumps({"state": "INVALID", "error": type(exc).__name__}, sort_keys=True))
        return 1
    print(json.dumps(observation.to_dict(), sort_keys=True))
    return 0 if observation.valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
