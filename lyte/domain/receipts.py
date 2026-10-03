"""Immutable receipt input contracts."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .canonical import canonical_json
from .truth import TruthLabel

_NAME = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$")
_SENSITIVE_KEYS = {
    "apikey",
    "accesstoken",
    "authtoken",
    "authorization",
    "bearertoken",
    "clientsecret",
    "cookie",
    "credential",
    "credentials",
    "csrftoken",
    "idtoken",
    "oauthtoken",
    "passphrase",
    "passwd",
    "password",
    "privatekey",
    "proxyauthorization",
    "refreshtoken",
    "secret",
    "sessiontoken",
    "setcookie",
    "token",
    "xsrftoken",
}


def is_sensitive_key(key: str) -> bool:
    """Match credential fields, including qualified names, on word boundaries.

    Collapse formatting differences while retaining boundaries so telemetry such
    as ``token_count`` and ``authorization_status`` remains usable. Compact
    spellings of compound credentials (for example ``APIKEY``) also match.
    """
    normalized = unicodedata.normalize("NFKC", key).strip()
    words = re.findall(r"[a-z0-9]+", normalized.casefold())
    if words and (words[-1] in _SENSITIVE_KEYS or "".join(words[-2:]) in _SENSITIVE_KEYS):
        return True
    normalized = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", normalized)
    normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", normalized)
    words = re.findall(r"[a-z0-9]+", normalized.casefold())
    return bool(words) and (words[-1] in _SENSITIVE_KEYS or "".join(words[-2:]) in _SENSITIVE_KEYS)


def assert_no_sensitive_keys(value: Any, *, path: str = "payload") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if is_sensitive_key(str(key)):
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
