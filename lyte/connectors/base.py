"""Shared, fail-closed connector contracts and bounded JSON helpers."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from lyte.domain import ReceiptDraft, TruthLabel, canonical_json

_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$")
_SENSITIVE_ATTRIBUTE_KEYS = {
    "access_token",
    "api_key",
    "auth_token",
    "authorization",
    "client_secret",
    "cookie",
    "password",
    "private_key",
    "refresh_token",
    "secret",
    "session_token",
    "set_cookie",
    "token",
}


class ConnectorError(RuntimeError):
    """Base class for connector policy and transport errors."""


class ConnectorPolicyError(ConnectorError):
    """A request attempted to exceed the connector's code-defined authority."""


class PayloadValidationError(ConnectorError, ValueError):
    """An inbound payload is malformed or outside the documented subset."""


class ConnectorState(StrEnum):
    OBSERVED = "OBSERVED"
    OBSERVED_PARTIAL = "OBSERVED_PARTIAL"
    NOT_MODIFIED = "NOT_MODIFIED"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class IdempotencyClaim:
    """A persistence-ready idempotency binding; it is not a durability claim."""

    key: str
    payload_sha256: str

    def __post_init__(self) -> None:
        if not _IDEMPOTENCY_KEY.fullmatch(self.key):
            raise PayloadValidationError("idempotency key must contain 16-128 safe characters")
        if not re.fullmatch(r"[0-9a-f]{64}", self.payload_sha256):
            raise PayloadValidationError("payload_sha256 must be a lowercase SHA-256 digest")

    @classmethod
    def from_payload(cls, key: str, payload: bytes) -> IdempotencyClaim:
        return cls(key=key, payload_sha256=hashlib.sha256(payload).hexdigest())

    def to_dict(self) -> dict[str, str]:
        return {
            "key_sha256": hashlib.sha256(self.key.encode("utf-8")).hexdigest(),
            "payload_sha256": self.payload_sha256,
        }


@dataclass(frozen=True, slots=True)
class ConnectorResult:
    """Generic read-only connector result with a truthful source state."""

    source_id: str
    state: ConnectorState
    records: tuple[Mapping[str, Any], ...]
    receipt: ReceiptDraft
    reason: str | None = None
    complete: bool = True
    metadata: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "state", ConnectorState(self.state))
        object.__setattr__(self, "records", tuple(dict(record) for record in self.records))
        object.__setattr__(self, "metadata", dict(self.metadata or {}))
        if self.state is ConnectorState.UNAVAILABLE:
            if not (self.reason or "").strip():
                raise ValueError("UNAVAILABLE connector results require a reason")
            if self.records:
                raise ValueError("UNAVAILABLE connector results cannot contain records")
        elif self.reason is not None:
            raise ValueError("available connector results cannot contain an unavailable reason")
        if self.state is ConnectorState.OBSERVED_PARTIAL and self.complete:
            raise ValueError("OBSERVED_PARTIAL results must declare complete=False")

    @property
    def truth_label(self) -> TruthLabel:
        return (
            TruthLabel.UNAVAILABLE
            if self.state is ConnectorState.UNAVAILABLE
            else self.receipt.truth_label
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "state": self.state.value,
            "truth_label": self.truth_label.value,
            "reason": self.reason,
            "complete": self.complete,
            "record_count": len(self.records),
            "records": [dict(record) for record in self.records],
            "metadata": dict(self.metadata or {}),
        }


def strict_json_loads(payload: bytes, *, max_bytes: int, path: str = "payload") -> Any:
    """Decode JSON without duplicate keys, non-finite numbers, or body overflow."""

    if not isinstance(payload, bytes):
        raise TypeError("payload must be bytes")
    if len(payload) > max_bytes:
        raise PayloadValidationError(f"{path} exceeds {max_bytes} bytes")
    if not payload:
        raise PayloadValidationError(f"{path} cannot be empty")

    def reject_constant(value: str) -> None:
        raise PayloadValidationError(f"{path} contains non-finite number {value}")

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise PayloadValidationError(f"{path} contains duplicate key {key!r}")
            result[key] = value
        return result

    try:
        decoded = json.loads(
            payload,
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except PayloadValidationError:
        raise
    except UnicodeDecodeError as exc:
        raise PayloadValidationError(f"{path} must be UTF-8 JSON") from exc
    except (json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise PayloadValidationError(f"{path} is malformed JSON") from exc
    try:
        assert_finite_json(decoded, path=path)
    except RecursionError as exc:
        raise PayloadValidationError(f"{path} exceeds the maximum supported nesting") from exc
    return decoded


def assert_finite_json(value: Any, *, path: str = "payload") -> None:
    """Validate that a decoded value can be represented by canonical JSON."""

    def walk(nested_value: Any, nested_path: str) -> None:
        if isinstance(nested_value, float) and not math.isfinite(nested_value):
            raise PayloadValidationError(f"{nested_path} must be finite")
        if isinstance(nested_value, Mapping):
            for key, nested in nested_value.items():
                if not isinstance(key, str):
                    raise PayloadValidationError(f"{nested_path} keys must be strings")
                walk(nested, f"{nested_path}.{key}")
        elif isinstance(nested_value, list):
            for index, nested in enumerate(nested_value):
                walk(nested, f"{nested_path}[{index}]")

    walk(value, path)
    try:
        canonical_json(value)
    except (TypeError, ValueError) as exc:
        raise PayloadValidationError(f"{path} is not canonical JSON data") from exc


def is_sensitive_attribute_key(key: str) -> bool:
    normalized = key.strip().lower().replace("-", "_").replace(".", "_")
    return normalized in _SENSITIVE_ATTRIBUTE_KEYS or normalized.endswith("_secret")


def strip_sensitive_attributes(
    attributes: Mapping[str, Any],
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Remove sensitive attribute values and report only the removed key names."""

    clean: dict[str, Any] = {}
    stripped: list[str] = []
    for raw_key, value in attributes.items():
        key = str(raw_key)
        if is_sensitive_attribute_key(key):
            stripped.append(key)
        else:
            clean[key] = value
    return clean, tuple(sorted(set(stripped)))


def require_object(value: Any, *, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PayloadValidationError(f"{path} must be an object")
    return value


def require_list(value: Any, *, path: str, maximum: int) -> list[Any]:
    if not isinstance(value, list):
        raise PayloadValidationError(f"{path} must be an array")
    if len(value) > maximum:
        raise PayloadValidationError(f"{path} exceeds the limit of {maximum}")
    return value


def require_fields(
    value: Mapping[str, Any],
    *,
    path: str,
    allowed: frozenset[str],
    required: frozenset[str] = frozenset(),
) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise PayloadValidationError(f"{path} contains unsupported field(s): {', '.join(unknown)}")
    missing = sorted(required - set(value))
    if missing:
        raise PayloadValidationError(f"{path} is missing required field(s): {', '.join(missing)}")


__all__ = [
    "ConnectorError",
    "ConnectorPolicyError",
    "ConnectorResult",
    "ConnectorState",
    "IdempotencyClaim",
    "PayloadValidationError",
    "assert_finite_json",
    "is_sensitive_attribute_key",
    "require_fields",
    "require_list",
    "require_object",
    "strict_json_loads",
    "strip_sensitive_attributes",
]
