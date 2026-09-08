"""Stable JSON projections for persistence records."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from lyte.domain import isoformat_z


def timestamp(value: datetime) -> str:
    """Restore UTC on SQLite values whose driver drops timezone metadata."""

    normalized = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return isoformat_z(normalized)


def operational_record(row: Any) -> dict[str, Any]:
    return {
        "schema": "szl.lyte.operational-view/v1",
        "id": row.id,
        "entity_kind": row.entity_kind,
        "entity_id": row.entity_id,
        "version": row.version,
        "name": row.name,
        "body": dict(row.body_json),
        "truth_label": row.truth_label,
        "evidence_refs": list(row.evidence_refs),
        "source_revision": row.source_revision,
        "observed_at": timestamp(row.observed_at) if row.observed_at else None,
        "created_at": timestamp(row.created_at),
        "record_hash": row.record_hash,
        "previous_hash": row.previous_hash,
        "metadata": dict(row.metadata_json),
    }


def operational_page(
    rows: Iterable[Any],
    *,
    limit: int,
    offset: int,
    data_mode: str,
) -> dict[str, Any]:
    items = [operational_record(row) for row in rows]
    return {
        "schema": "szl.lyte.operational-page/v1",
        "items": items,
        "count": len(items),
        "limit": limit,
        "offset": offset,
        "next_offset": offset + len(items) if len(items) == limit else None,
        "data_mode": data_mode,
        "truth_label": "SAMPLE" if data_mode == "SAMPLE" else "REPORTED",
    }


def receipt_record(row: Any) -> dict[str, Any]:
    return {
        "schema": "szl.lyte.receipt-view/v1",
        "id": row.id,
        "kind": row.kind,
        "subject_type": row.subject_type,
        "subject_id": row.subject_id,
        "truth_label": row.truth_label,
        "payload": dict(row.payload_json),
        "payload_sha256": row.payload_sha256,
        "evidence_refs": list(row.evidence_refs),
        "sequence": row.sequence,
        "record_hash": row.record_hash,
        "previous_hash": row.previous_hash,
        "created_at": timestamp(row.created_at),
    }


__all__ = ["operational_page", "operational_record", "receipt_record", "timestamp"]
