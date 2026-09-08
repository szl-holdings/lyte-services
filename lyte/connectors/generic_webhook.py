"""Strict governed-event webhook verification with HMAC and replay defense."""

from __future__ import annotations

import hashlib
import hmac
import re
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from lyte.domain import (
    ReceiptDraft,
    Scope,
    TruthLabel,
    assert_no_sensitive_keys,
    canonical_json,
    isoformat_z,
    sha256_text,
)

from .base import (
    IdempotencyClaim,
    PayloadValidationError,
    require_fields,
    require_list,
    require_object,
    strict_json_loads,
)

_SAFE_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$")
_NONCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$")
_SIGNATURE = re.compile(r"^sha256=([0-9a-f]{64})$")


class GovernedEventError(RuntimeError):
    """Base class for governed webhook verification failures."""


class SignatureVerificationError(GovernedEventError):
    pass


class TimestampSkewError(GovernedEventError):
    pass


class ReplayDetectedError(GovernedEventError):
    pass


class WebhookIdempotencyConflict(GovernedEventError):
    pass


class ReplayProtectionUnavailable(GovernedEventError):
    pass


@dataclass(frozen=True, slots=True)
class ReplayClaim:
    nonce: str
    idempotency: IdempotencyClaim
    replayed: bool


class ReplayProtector:
    """Bounded process-local nonce and idempotency guard.

    Production callers should additionally persist ``IdempotencyClaim`` using
    the tenant/workspace-scoped store. The local guard never claims durable or
    multi-replica replay protection.
    """

    def __init__(self, *, retention_seconds: int = 900, max_entries: int = 20_000) -> None:
        if not 60 <= retention_seconds <= 86_400:
            raise ValueError("retention_seconds must be between 60 and 86400")
        if not 100 <= max_entries <= 1_000_000:
            raise ValueError("max_entries must be between 100 and 1000000")
        self.retention_seconds = retention_seconds
        self.max_entries = max_entries
        self._nonces: dict[str, float] = {}
        self._idempotency: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()

    def claim(
        self,
        *,
        nonce: str,
        idempotency_key: str,
        payload_sha256: str,
        now: datetime,
    ) -> ReplayClaim:
        if not _NONCE.fullmatch(nonce):
            raise PayloadValidationError("nonce must contain 16-128 safe characters")
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        idempotency = IdempotencyClaim(idempotency_key, payload_sha256)
        current = now.timestamp()
        expires = current + self.retention_seconds
        with self._lock:
            self._nonces = {key: expiry for key, expiry in self._nonces.items() if expiry > current}
            self._idempotency = {
                key: value for key, value in self._idempotency.items() if value[1] > current
            }
            if nonce in self._nonces:
                raise ReplayDetectedError("nonce has already been used")
            if len(self._nonces) >= self.max_entries:
                raise ReplayProtectionUnavailable("nonce registry is at its safe capacity")
            existing = self._idempotency.get(idempotency_key)
            if existing is not None and existing[0] != payload_sha256:
                raise WebhookIdempotencyConflict(
                    "idempotency key is already bound to a different payload"
                )
            replayed = existing is not None
            if existing is None and len(self._idempotency) >= self.max_entries:
                raise ReplayProtectionUnavailable("idempotency registry is at its safe capacity")
            self._nonces[nonce] = expires
            self._idempotency[idempotency_key] = (payload_sha256, expires)
        return ReplayClaim(nonce=nonce, idempotency=idempotency, replayed=replayed)


@dataclass(frozen=True, slots=True)
class GovernedEvent:
    schema_version: str
    event_id: str
    source_id: str
    event_type: str
    subject_type: str
    subject_id: str
    occurred_at: datetime
    truth_label: TruthLabel
    attributes: Mapping[str, Any]
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "truth_label", TruthLabel(self.truth_label))
        object.__setattr__(self, "attributes", dict(self.attributes))
        object.__setattr__(self, "evidence_refs", tuple(self.evidence_refs))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "source_id": self.source_id,
            "event_type": self.event_type,
            "subject_type": self.subject_type,
            "subject_id": self.subject_id,
            "occurred_at": isoformat_z(self.occurred_at),
            "truth_label": self.truth_label.value,
            "attributes": dict(self.attributes),
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True, slots=True)
class VerifiedGovernedEvent:
    scope: Scope
    event: GovernedEvent
    replay_claim: ReplayClaim
    signature_verified: bool
    payload_sha256: str
    receipt: ReceiptDraft

    @property
    def replayed(self) -> bool:
        return self.replay_claim.replayed

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.scope.to_dict(),
            "event": self.event.to_dict(),
            "signature_verified": self.signature_verified,
            "replayed": self.replayed,
            "payload_sha256": self.payload_sha256,
            "idempotency": self.replay_claim.idempotency.to_dict(),
        }


def sign_governed_event(
    secret: bytes,
    *,
    timestamp: str,
    nonce: str,
    body: bytes,
) -> str:
    """Create the documented ``sha256=`` signature for test/client tooling."""

    if not isinstance(secret, bytes) or len(secret) < 32:
        raise ValueError("HMAC secret must contain at least 32 bytes")
    signing_input = timestamp.encode("ascii") + b"." + nonce.encode("ascii") + b"." + body
    return "sha256=" + hmac.new(secret, signing_input, hashlib.sha256).hexdigest()


class GovernedEventVerifier:
    """Verify a source-allowlisted event before any persistence side effect."""

    def __init__(
        self,
        *,
        allowed_sources: set[str] | frozenset[str] | tuple[str, ...],
        secret: bytes | None,
        replay_protector: ReplayProtector | None = None,
        max_body_bytes: int = 256_000,
        max_timestamp_skew_seconds: int = 300,
        allow_unsigned: bool = False,
    ) -> None:
        sources = frozenset(str(item).strip() for item in allowed_sources)
        if not sources or any(not _SAFE_ID.fullmatch(item) for item in sources):
            raise ValueError("allowed_sources must contain valid source identifiers")
        if secret is not None and (not isinstance(secret, bytes) or len(secret) < 32):
            raise ValueError("HMAC secret must contain at least 32 bytes")
        if secret is None and not allow_unsigned:
            raise ValueError("an HMAC secret is required unless unsigned mode is explicit")
        if not 1_024 <= max_body_bytes <= 2_000_000:
            raise ValueError("max_body_bytes must be between 1024 and 2000000")
        if not 30 <= max_timestamp_skew_seconds <= 3_600:
            raise ValueError("max_timestamp_skew_seconds must be between 30 and 3600")
        self.allowed_sources = sources
        self._secret = secret
        self._allow_unsigned = allow_unsigned
        self.replay_protector = replay_protector or ReplayProtector()
        self.max_body_bytes = max_body_bytes
        self.max_timestamp_skew_seconds = max_timestamp_skew_seconds

    def verify(
        self,
        body: bytes,
        *,
        scope: Scope,
        signature: str | None,
        timestamp: str,
        nonce: str,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> VerifiedGovernedEvent:
        current = now or datetime.now(UTC)
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        try:
            signed_at_seconds = int(timestamp)
        except (TypeError, ValueError) as exc:
            raise TimestampSkewError("signature timestamp must be Unix seconds") from exc
        if str(signed_at_seconds) != timestamp or signed_at_seconds < 0:
            raise TimestampSkewError("signature timestamp must be canonical Unix seconds")
        if abs(current.timestamp() - signed_at_seconds) > self.max_timestamp_skew_seconds:
            raise TimestampSkewError("signature timestamp is outside the permitted skew")
        if not _NONCE.fullmatch(nonce):
            raise PayloadValidationError("nonce must contain 16-128 safe characters")

        signature_verified = False
        if self._secret is not None:
            if not isinstance(signature, str) or not _SIGNATURE.fullmatch(signature):
                raise SignatureVerificationError("HMAC signature is missing or malformed")
            expected = sign_governed_event(
                self._secret,
                timestamp=timestamp,
                nonce=nonce,
                body=body,
            )
            if not hmac.compare_digest(signature, expected):
                raise SignatureVerificationError("HMAC signature verification failed")
            signature_verified = True
        elif not self._allow_unsigned:
            raise SignatureVerificationError("HMAC verification is unavailable")

        decoded = strict_json_loads(
            body,
            max_bytes=self.max_body_bytes,
            path="governed_event",
        )
        event = self._parse_event(decoded)
        if event.source_id not in self.allowed_sources:
            raise GovernedEventError("event source is not in the code-defined allowlist")
        payload_sha = hashlib.sha256(body).hexdigest()
        replay_claim = self.replay_protector.claim(
            nonce=nonce,
            idempotency_key=idempotency_key,
            payload_sha256=payload_sha,
            now=current,
        )
        receipt = ReceiptDraft(
            kind="ingest.governed_event.verified",
            subject_type=event.subject_type,
            subject_id=event.subject_id,
            payload={
                **scope.to_dict(),
                "event_id": event.event_id,
                "source_id": event.source_id,
                "event_type": event.event_type,
                "occurred_at": isoformat_z(event.occurred_at),
                "signature_verified": signature_verified,
                "unsigned_mode": not signature_verified,
                "replayed": replay_claim.replayed,
                "payload_sha256": payload_sha,
                "idempotency_key_sha256": sha256_text(idempotency_key),
            },
            truth_label=event.truth_label,
            evidence_refs=event.evidence_refs,
        )
        return VerifiedGovernedEvent(
            scope=scope,
            event=event,
            replay_claim=replay_claim,
            signature_verified=signature_verified,
            payload_sha256=payload_sha,
            receipt=receipt,
        )

    @staticmethod
    def _parse_event(value: Any) -> GovernedEvent:
        event = require_object(value, path="governed_event")
        fields = frozenset(
            {
                "schema_version",
                "event_id",
                "source_id",
                "event_type",
                "subject_type",
                "subject_id",
                "occurred_at",
                "truth_label",
                "attributes",
                "evidence_refs",
            }
        )
        require_fields(event, path="governed_event", allowed=fields, required=fields)
        if event["schema_version"] != "lyte.event.v1":
            raise PayloadValidationError("governed_event.schema_version must be lyte.event.v1")
        strings: dict[str, str] = {}
        for field in (
            "event_id",
            "source_id",
            "event_type",
            "subject_type",
            "subject_id",
        ):
            raw = event[field]
            if not isinstance(raw, str) or not _SAFE_ID.fullmatch(raw):
                raise PayloadValidationError(f"governed_event.{field} is invalid")
            strings[field] = raw
        occurred_raw = event["occurred_at"]
        if not isinstance(occurred_raw, str) or len(occurred_raw) > 64:
            raise PayloadValidationError("governed_event.occurred_at is invalid")
        try:
            occurred_at = datetime.fromisoformat(occurred_raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise PayloadValidationError("governed_event.occurred_at is invalid") from exc
        if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
            raise PayloadValidationError("governed_event.occurred_at requires a timezone")
        try:
            truth_label = TruthLabel(event["truth_label"])
        except (TypeError, ValueError) as exc:
            raise PayloadValidationError("governed_event.truth_label is invalid") from exc
        if truth_label in {TruthLabel.UNAVAILABLE, TruthLabel.ROADMAP}:
            raise PayloadValidationError(
                "governed_event with payload data cannot be UNAVAILABLE or ROADMAP"
            )
        attributes = require_object(event["attributes"], path="governed_event.attributes")
        if len(attributes) > 64:
            raise PayloadValidationError("governed_event.attributes exceeds the limit of 64")
        try:
            assert_no_sensitive_keys(attributes, path="governed_event.attributes")
            canonical_json(attributes)
        except (TypeError, ValueError) as exc:
            raise PayloadValidationError(
                "governed_event.attributes contains forbidden or invalid data"
            ) from exc
        refs_raw = require_list(
            event["evidence_refs"], path="governed_event.evidence_refs", maximum=32
        )
        refs: list[str] = []
        for index, raw in enumerate(refs_raw):
            if not isinstance(raw, str) or not raw.strip() or len(raw) > 512:
                raise PayloadValidationError(f"governed_event.evidence_refs[{index}] is invalid")
            refs.append(raw.strip())
        return GovernedEvent(
            schema_version="lyte.event.v1",
            event_id=strings["event_id"],
            source_id=strings["source_id"],
            event_type=strings["event_type"],
            subject_type=strings["subject_type"],
            subject_id=strings["subject_id"],
            occurred_at=occurred_at,
            truth_label=truth_label,
            attributes=dict(attributes),
            evidence_refs=tuple(dict.fromkeys(refs)),
        )


GenericWebhookVerifier = GovernedEventVerifier

__all__ = [
    "GenericWebhookVerifier",
    "GovernedEvent",
    "GovernedEventError",
    "GovernedEventVerifier",
    "ReplayClaim",
    "ReplayDetectedError",
    "ReplayProtectionUnavailable",
    "ReplayProtector",
    "SignatureVerificationError",
    "TimestampSkewError",
    "VerifiedGovernedEvent",
    "WebhookIdempotencyConflict",
    "sign_governed_event",
]
