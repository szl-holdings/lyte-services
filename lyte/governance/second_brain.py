"""Tenant-scoped Second Brain contracts that never retain caller tokens."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from lyte.domain import Scope, TruthLabel, assert_no_sensitive_keys, sha256_text

_TOKEN = re.compile(r"^[A-Za-z0-9._~-]{32,512}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


def digest_scope_token(scope: Scope, token: str) -> str:
    """Return a domain-separated digest; the raw token must be discarded."""
    if not isinstance(token, str) or not _TOKEN.fullmatch(token):
        raise ValueError("scope token must be a caller-held 32-512 character high-entropy token")
    basis = (
        f"szl.lyte.second-brain.scope/v1\x00{scope.tenant_id}\x00{scope.workspace_id}\x00{token}"
    )
    return sha256_text(basis)


class MemoryKind(StrEnum):
    OBSERVATION = "OBSERVATION"
    APPROVED_KNOWLEDGE = "APPROVED_KNOWLEDGE"


@dataclass(frozen=True, slots=True)
class MemoryDraft:
    """Summary-only memory input.

    The contract accepts a precomputed scope digest, never the caller token.
    Raw prompts, responses, credentials, and arbitrary source bodies are not
    fields in the model.
    """

    scope_digest: str
    kind: MemoryKind
    summary: str
    truth_label: TruthLabel
    evidence_refs: tuple[str, ...]
    subjects: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not _DIGEST.fullmatch(self.scope_digest):
            raise ValueError("scope_digest must be a lowercase SHA-256 digest")
        object.__setattr__(self, "kind", MemoryKind(self.kind))
        object.__setattr__(self, "truth_label", TruthLabel(self.truth_label))
        summary = " ".join(self.summary.split())
        if not summary or len(summary) > 2_000:
            raise ValueError("memory summary must contain 1-2000 characters")
        refs = tuple(dict.fromkeys(item.strip() for item in self.evidence_refs if item.strip()))
        if not refs:
            raise ValueError("memory records require at least one evidence reference")
        if self.kind is MemoryKind.APPROVED_KNOWLEDGE and self.truth_label in {
            TruthLabel.SAMPLE,
            TruthLabel.ROADMAP,
            TruthLabel.UNAVAILABLE,
        }:
            raise ValueError("approved knowledge cannot be SAMPLE, ROADMAP, or UNAVAILABLE")
        subjects = tuple(
            dict.fromkeys(" ".join(item.split()) for item in self.subjects if item.strip())
        )
        metadata = dict(self.metadata)
        assert_no_sensitive_keys(metadata, path="memory.metadata")
        object.__setattr__(self, "summary", summary)
        object.__setattr__(self, "evidence_refs", refs)
        object.__setattr__(self, "subjects", subjects)
        object.__setattr__(self, "metadata", metadata)
