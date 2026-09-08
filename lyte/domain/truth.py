"""Truth labels are data contracts, not presentation badges."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from .canonical import isoformat_z


class TruthLabel(StrEnum):
    MEASURED = "MEASURED"
    REPORTED = "REPORTED"
    MODELED = "MODELED"
    SAMPLE = "SAMPLE"
    ROADMAP = "ROADMAP"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class TruthValue[T]:
    """A value and the evidence class that permits it to exist.

    ``UNAVAILABLE`` is represented as ``None`` with an explicit reason. No
    caller can silently turn missing data into a numerical zero.
    """

    value: T | None
    label: TruthLabel
    reason: str | None = None
    evidence_refs: tuple[str, ...] = ()
    observed_at: datetime | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.label, TruthLabel):
            object.__setattr__(self, "label", TruthLabel(self.label))
        reason = (self.reason or "").strip() or None
        object.__setattr__(self, "reason", reason)
        object.__setattr__(
            self,
            "evidence_refs",
            tuple(dict.fromkeys(item.strip() for item in self.evidence_refs if item.strip())),
        )
        object.__setattr__(self, "metadata", dict(self.metadata))
        if self.label is TruthLabel.UNAVAILABLE:
            if self.value is not None:
                raise ValueError("UNAVAILABLE values must be null")
            if reason is None:
                raise ValueError("UNAVAILABLE values require a reason")
        elif self.value is None:
            raise ValueError(f"{self.label.value} values cannot be null")
        if self.observed_at is not None:
            isoformat_z(self.observed_at)

    @property
    def available(self) -> bool:
        return self.label is not TruthLabel.UNAVAILABLE

    @classmethod
    def unavailable(
        cls,
        reason: str,
        *,
        evidence_refs: tuple[str, ...] = (),
        metadata: Mapping[str, Any] | None = None,
    ) -> TruthValue[T]:
        return cls(
            value=None,
            label=TruthLabel.UNAVAILABLE,
            reason=reason,
            evidence_refs=evidence_refs,
            metadata=metadata or {},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "truth_label": self.label.value,
            "reason": self.reason,
            "evidence_refs": list(self.evidence_refs),
            "observed_at": isoformat_z(self.observed_at) if self.observed_at else None,
            "metadata": dict(self.metadata),
        }
