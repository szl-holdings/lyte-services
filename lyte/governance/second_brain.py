"""Tenant/workspace Second Brain contracts that never retain caller tokens."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from lyte.domain import Scope, TruthLabel, assert_no_sensitive_keys, sha256_text

_TOKEN = re.compile(r"^[A-Za-z0-9._~-]{32,512}$")
_SUBJECT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/~-]{0,255}$")
SUBJECT_DIMENSIONS = frozenset({"service", "journey", "outcome", "incident", "decision"})
_NON_MEMORY_FIELDS = frozenset(
    {"scopedigest", "sessiondigest", "sessionhash", "rawprompt", "rawresponse"}
)


def _assert_summary_metadata(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", str(key).casefold())
            if normalized in _NON_MEMORY_FIELDS:
                raise ValueError(f"session and raw content fields are forbidden in memory: {key}")
            _assert_summary_metadata(nested)
    elif isinstance(value, list | tuple):
        for nested in value:
            _assert_summary_metadata(nested)


def digest_scope_token(scope: Scope, token: str) -> str:
    """Return a transient domain-separated digest; the raw token must be discarded.

    The returned digest is a request-local correlation scope. It is deliberately
    absent from :class:`MemoryDraft`, so neither it nor the caller token can be
    written to the enterprise memory repository.
    """
    if not isinstance(token, str) or not _TOKEN.fullmatch(token):
        raise ValueError("scope token must be a caller-held 32-512 character high-entropy token")
    basis = (
        f"szl.lyte.second-brain.scope/v1\x00{scope.tenant_id}\x00{scope.workspace_id}\x00{token}"
    )
    return sha256_text(basis)


def enterprise_memory_partition(scope: Scope) -> str:
    """Return the stable internal partition for durable workspace memory.

    This value contains no caller/session material. The database's legacy
    ``scope_digest`` physical column stores only this tenant/workspace-derived
    partition for newly written records.
    """

    return sha256_text(
        f"szl.lyte.enterprise-memory.partition/v1\x00{scope.tenant_id}\x00{scope.workspace_id}"
    )


class MemoryKind(StrEnum):
    OBSERVATION = "OBSERVATION"
    APPROVED_KNOWLEDGE = "APPROVED_KNOWLEDGE"


@dataclass(frozen=True, slots=True)
class MemoryDraft:
    """Summary-only memory input.

    Raw prompts, responses, credentials, session tokens, and session digests
    are not fields in the model. Durable partitioning is derived from the
    authenticated tenant/workspace by the persistence layer.
    """

    kind: MemoryKind
    summary: str
    truth_label: TruthLabel
    evidence_refs: tuple[str, ...]
    subjects: tuple[str, ...] = ()
    subject_dimensions: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", MemoryKind(self.kind))
        object.__setattr__(self, "truth_label", TruthLabel(self.truth_label))
        summary = " ".join(self.summary.split())
        if not summary or len(summary) > 2_000:
            raise ValueError("memory summary must contain 1-2000 characters")
        refs = tuple(dict.fromkeys(item.strip() for item in self.evidence_refs if item.strip()))
        if not refs:
            raise ValueError("memory records require at least one evidence reference")
        if len(refs) > 64 or any(len(ref) > 512 for ref in refs):
            raise ValueError("memory evidence references must be bounded to 64 x 512 characters")
        if self.kind is MemoryKind.APPROVED_KNOWLEDGE and self.truth_label in {
            TruthLabel.SAMPLE,
            TruthLabel.ROADMAP,
            TruthLabel.UNAVAILABLE,
        }:
            raise ValueError("approved knowledge cannot be SAMPLE, ROADMAP, or UNAVAILABLE")
        subjects = tuple(
            dict.fromkeys(" ".join(item.split()) for item in self.subjects if item.strip())
        )
        if len(subjects) > 100:
            raise ValueError("memory records permit at most 100 subjects")
        dimensions: dict[str, tuple[str, ...]] = {}
        for raw_dimension, raw_values in self.subject_dimensions.items():
            dimension = str(raw_dimension).strip().lower()
            if dimension not in SUBJECT_DIMENSIONS:
                raise ValueError(f"unsupported memory subject dimension: {dimension}")
            values = tuple(
                dict.fromkeys(str(item).strip() for item in raw_values if str(item).strip())
            )
            if len(values) > 100:
                raise ValueError(f"memory subject dimension {dimension} permits at most 100 values")
            if any(_SUBJECT.fullmatch(value) is None for value in values):
                raise ValueError(
                    f"memory subject dimension {dimension} contains an invalid identifier"
                )
            if values:
                dimensions[dimension] = values
        metadata = dict(self.metadata)
        if "subject_dimensions" in metadata:
            raise ValueError("memory.metadata.subject_dimensions is reserved")
        assert_no_sensitive_keys(metadata, path="memory.metadata")
        _assert_summary_metadata(metadata)
        object.__setattr__(self, "summary", summary)
        object.__setattr__(self, "evidence_refs", refs)
        object.__setattr__(self, "subjects", subjects)
        object.__setattr__(self, "subject_dimensions", dimensions)
        object.__setattr__(self, "metadata", metadata)
