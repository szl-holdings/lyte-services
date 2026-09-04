"""Strict request contracts for the Lyte Enterprise API."""
from __future__ import annotations

import math
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

AXIS_ID = re.compile(r"^[a-z][a-z0-9_.-]{1,63}$")
ACTION_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,63}$")
RECEIPT_ID = re.compile(r"^[0-9a-f]{64}$")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AnalyzeRequest(StrictModel):
    services: list[dict[str, Any]] = Field(min_length=1, max_length=100)
    outcomes: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    traces: list[dict[str, Any]] = Field(default_factory=list, max_length=500)
    evidence_refs: list[str] = Field(default_factory=list, max_length=64)

    @field_validator("evidence_refs")
    @classmethod
    def validate_evidence_refs(cls, values: list[str]) -> list[str]:
        clean = [" ".join(str(value).split()) for value in values]
        if any(not value or len(value) > 240 for value in clean):
            raise ValueError("evidence references must contain 1-240 characters")
        if len(clean) != len(set(clean)):
            raise ValueError("evidence references must be unique")
        return clean


class JourneyRequest(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    stages: list[dict[str, Any]] = Field(min_length=1, max_length=50)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        clean = " ".join(value.split())
        if not clean:
            raise ValueError("journey name must not be blank")
        return clean


class SpanBatch(StrictModel):
    spans: list[dict[str, Any]] = Field(min_length=1, max_length=5000)


class EventBatch(StrictModel):
    events: list[dict[str, Any]] = Field(min_length=1, max_length=2000)


class AskRequest(StrictModel):
    question: str = Field(min_length=1, max_length=500)

    @field_validator("question")
    @classmethod
    def clean_question(cls, value: str) -> str:
        clean = " ".join(value.split())
        if not clean:
            raise ValueError("question must not be blank")
        return clean


class CompileRequest(StrictModel):
    cell: str = Field(min_length=1, max_length=32)
    signal: str = Field(default="", max_length=1000)
    prev_hash: str = Field(default="0" * 64, min_length=64, max_length=64)

    @field_validator("prev_hash")
    @classmethod
    def validate_prev_hash(cls, value: str) -> str:
        clean = value.strip().lower()
        if RECEIPT_ID.fullmatch(clean) is None:
            raise ValueError("prev_hash must be 64 lowercase hex characters")
        return clean


class ActRequest(StrictModel):
    cell: str = Field(min_length=1, max_length=32)
    payload: dict[str, Any] = Field(default_factory=dict)


class HatunRequest(StrictModel):
    intent: str = Field(min_length=1, max_length=240)
    requested_action: str = Field(default="observe.review", min_length=2, max_length=64)
    axes: dict[str, float]
    evidence_receipt_ids: list[str] = Field(default_factory=list, max_length=64)

    @field_validator("intent")
    @classmethod
    def clean_intent(cls, value: str) -> str:
        clean = " ".join(value.split())
        if not clean:
            raise ValueError("intent must not be blank")
        return clean

    @field_validator("requested_action")
    @classmethod
    def validate_action(cls, value: str) -> str:
        clean = value.strip()
        if ACTION_ID.fullmatch(clean) is None:
            raise ValueError("requested_action is invalid")
        return clean

    @field_validator("axes")
    @classmethod
    def validate_axes(cls, values: dict[str, float]) -> dict[str, float]:
        if not 2 <= len(values) <= 16:
            raise ValueError("axes must contain between 2 and 16 values")
        clean: dict[str, float] = {}
        for raw_name, raw_value in values.items():
            name = raw_name.strip().lower()
            numeric = float(raw_value)
            if AXIS_ID.fullmatch(name) is None:
                raise ValueError(f"invalid axis identifier: {raw_name!r}")
            if not math.isfinite(numeric) or not 0.0 <= numeric <= 1.0:
                raise ValueError(f"axis {name!r} must be finite and in [0,1]")
            clean[name] = numeric
        return clean

    @field_validator("evidence_receipt_ids")
    @classmethod
    def validate_receipts(cls, values: list[str]) -> list[str]:
        clean = [value.strip().lower() for value in values]
        if any(RECEIPT_ID.fullmatch(value) is None for value in clean):
            raise ValueError("evidence receipt IDs must be 64 lowercase hex characters")
        if len(clean) != len(set(clean)):
            raise ValueError("evidence receipt IDs must be unique")
        return clean


__all__ = [
    "ActRequest",
    "AnalyzeRequest",
    "AskRequest",
    "CompileRequest",
    "EventBatch",
    "HatunRequest",
    "JourneyRequest",
    "SpanBatch",
    "StrictModel",
]
