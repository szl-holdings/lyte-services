"""Immutable receipt input contracts."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .canonical import canonical_json
from .truth import TruthLabel

_NAME = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$")
_SENSITIVE_KEYS = {
    "api_key",
    "authorization",
    "cookie",
    "password",
    "private_key",
    "secret",
    "set_cookie",
    "token",
}


def assert_no_sensitive_keys(value: Any, *, path: str = "payload") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            if normalized in _SENSITIVE_KEYS or normalized.endswith("_secret"):
                raise ValueError(f"sensitive field is forbidden at {path}.{key}")
            assert_no_sensitive_keys(nested, path=f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            assert_no_sensitive_keys(nested, path=f"{path}[{index}]")


@dataclass(frozen=True, slots=True)
class ReceiptDraft:
    kind: str
    subject_type: str
    subject_id: str
    payload: Mapping[str, Any]
    truth_label: TruthLabel
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name, value in (
            ("kind", self.kind),
            ("subject_type", self.subject_type),
        ):
            if not _NAME.fullmatch(value):
                raise ValueError(f"{name} must match {_NAME.pattern}")
        subject_id = self.subject_id.strip()
        if not subject_id or len(subject_id) > 256:
            raise ValueError("subject_id must contain 1-256 characters")
        payload = dict(self.payload)
        assert_no_sensitive_keys(payload)
        canonical_json(payload)
        object.__setattr__(self, "subject_id", subject_id)
        object.__setattr__(self, "payload", payload)
        object.__setattr__(self, "truth_label", TruthLabel(self.truth_label))
        object.__setattr__(
            self,
            "evidence_refs",
            tuple(dict.fromkeys(item.strip() for item in self.evidence_refs if item.strip())),
        )
