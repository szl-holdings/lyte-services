"""Session-scoped Second Brain storage and deterministic Lyte answers."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from .core import (
    MAX_MEMORY_PAYLOAD_BYTES,
    RECEIPT_ID,
    SESSION_TOKEN,
    canonical_json,
    sha256_json,
)

_ALLOWED_TRUTH = {
    "MEASURED",
    "REPORTED",
    "MODELED",
    "SAMPLE",
    "ROADMAP",
    "UNAVAILABLE",
}


def session_scope(token: str) -> str:
    """Validate a caller-held token and return its non-reversible scope."""
    value = str(token).strip()
    if SESSION_TOKEN.fullmatch(value) is None:
        raise ValueError(
            "X-SZL-Session must contain 32-128 high-entropy characters "
            "using A-Z, a-z, 0-9, period, underscore, tilde, or hyphen"
        )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def observation_receipt(
    *,
    scope: str,
    kind: str,
    payload: Mapping[str, Any],
    truth_label: str,
    source_url: str,
    observed_at: float | None = None,
) -> dict[str, Any]:
    """Mint a source-safe SHA-256 receipt for one bounded observation."""
    if RECEIPT_ID.fullmatch(scope) is None:
        raise ValueError("session scope must be a SHA-256 digest")
    label = str(truth_label).upper()
    if label not in _ALLOWED_TRUTH:
        raise ValueError("unsupported truth label")
    serialized = canonical_json(payload).encode("utf-8")
    if len(serialized) > MAX_MEMORY_PAYLOAD_BYTES:
        raise ValueError("memory payload exceeds the configured byte budget")
    timestamp = float(time.time() if observed_at is None else observed_at)
    if timestamp < 0:
        raise ValueError("observed_at must be non-negative")
    basis = {
        "schema": "szl.lyte-observation-receipt/v3",
        "kind": str(kind)[:80],
        "session_scope_sha256": scope,
        "payload_sha256": hashlib.sha256(serialized).hexdigest(),
        "source_url": str(source_url)[:500],
        "observed_at": timestamp,
        "truth_label": label,
        "signature_claimed": False,
        "raw_session_token_recorded": False,
        "effectors_enabled": False,
    }
    return {
        **basis,
        "receipt_id": sha256_json(basis),
        "receipt_algorithm": "SHA-256",
    }


class SessionLedger:
    """Bounded append-only SQLite memory partitioned by hashed session scope."""

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        configured = path or os.environ.get(
            "LYTE_STATE_PATH", "/tmp/lyte-enterprise.sqlite3"
        )
        self.path = Path(configured)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            str(self.path),
            timeout=10,
            isolation_level=None,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS observations (
                    receipt_id TEXT PRIMARY KEY,
                    session_scope_sha256 TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    observed_at REAL NOT NULL,
                    truth_label TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    summary_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS observations_scope_time
                ON observations(session_scope_sha256, observed_at DESC)
                """
            )

    def append(
        self,
        scope: str,
        *,
        kind: str,
        receipt: Mapping[str, Any],
        summary: Mapping[str, Any],
    ) -> None:
        """Append a receipt summary without persisting caller secrets."""
        if RECEIPT_ID.fullmatch(scope) is None:
            raise ValueError("session scope must be a SHA-256 digest")
        receipt_id = str(receipt.get("receipt_id") or "")
        if RECEIPT_ID.fullmatch(receipt_id) is None:
            raise ValueError("receipt_id must be a SHA-256 digest")
        if receipt.get("session_scope_sha256") != scope:
            raise ValueError("receipt scope does not match the caller scope")
        safe_summary = json.loads(canonical_json(summary))
        encoded = canonical_json(safe_summary)
        if len(encoded.encode("utf-8")) > MAX_MEMORY_PAYLOAD_BYTES:
            raise ValueError("memory summary exceeds the configured byte budget")
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT OR IGNORE INTO observations (
                    receipt_id, session_scope_sha256, kind, observed_at,
                    truth_label, source_url, payload_sha256, summary_json,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt_id,
                    scope,
                    str(kind)[:80],
                    float(receipt.get("observed_at") or time.time()),
                    str(receipt.get("truth_label") or "UNAVAILABLE"),
                    str(receipt.get("source_url") or "")[:500],
                    str(receipt.get("payload_sha256") or ""),
                    encoded,
                    time.time(),
                ),
            )
            connection.commit()

    def recent(
        self,
        scope: str,
        *,
        limit: int = 25,
        kind: str | None = None,
    ) -> list[dict[str, Any]]:
        if RECEIPT_ID.fullmatch(scope) is None:
            raise ValueError("session scope must be a SHA-256 digest")
        bounded = max(1, min(int(limit), 100))
        query = (
            """
            SELECT receipt_id, kind, observed_at, truth_label, source_url,
                   payload_sha256, summary_json
            FROM observations
            WHERE session_scope_sha256 = ?
            """
        )
        parameters: list[Any] = [scope]
        if kind:
            query += " AND kind = ?"
            parameters.append(str(kind)[:80])
        query += " ORDER BY observed_at DESC LIMIT ?"
        parameters.append(bounded)
        with self._lock, self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [
            {
                "receipt_id": row["receipt_id"],
                "kind": row["kind"],
                "observed_at": row["observed_at"],
                "truth_label": row["truth_label"],
                "source_url": row["source_url"],
                "payload_sha256": row["payload_sha256"],
                "summary": json.loads(row["summary_json"]),
            }
            for row in rows
        ]

    def receipt_ids(self, scope: str) -> set[str]:
        return {
            item["receipt_id"]
            for item in self.recent(scope, limit=100)
            if isinstance(item.get("receipt_id"), str)
        }

    def get(self, scope: str, receipt_id: str) -> dict[str, Any] | None:
        if RECEIPT_ID.fullmatch(scope) is None:
            raise ValueError("session scope must be a SHA-256 digest")
        if RECEIPT_ID.fullmatch(receipt_id) is None:
            return None
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT receipt_id, kind, observed_at, truth_label, source_url,
                       payload_sha256, summary_json
                FROM observations
                WHERE session_scope_sha256 = ? AND receipt_id = ?
                """,
                (scope, receipt_id),
            ).fetchone()
        if row is None:
            return None
        return {
            "receipt_id": row["receipt_id"],
            "kind": row["kind"],
            "observed_at": row["observed_at"],
            "truth_label": row["truth_label"],
            "source_url": row["source_url"],
            "payload_sha256": row["payload_sha256"],
            "summary": json.loads(row["summary_json"]),
        }

    def status(self) -> dict[str, Any]:
        with self._lock, self._connect() as connection:
            counts = connection.execute(
                """
                SELECT COUNT(*) AS observations,
                       COUNT(DISTINCT session_scope_sha256) AS sessions
                FROM observations
                """
            ).fetchone()
        return {
            "durability": "SQLITE_APPEND_ONLY",
            "path_kind": "CONFIGURED_LOCAL_VOLUME",
            "sessions": int(counts["sessions"]),
            "observations": int(counts["observations"]),
            "scope": "SHA256_CALLER_SESSION",
            "raw_session_tokens_recorded": False,
            "max_payload_bytes": MAX_MEMORY_PAYLOAD_BYTES,
        }


def _analysis_answer(
    question: str,
    records: Sequence[Mapping[str, Any]],
) -> tuple[str, list[str], list[str], str]:
    needle = question.casefold()
    analyses = [item for item in records if item.get("kind") == "analysis"]
    journeys = [item for item in records if item.get("kind") == "journey"]
    sources = [item for item in records if item.get("kind") == "source"]

    if any(term in needle for term in ("revenue", "cost", "impact")):
        values = [
            float(item.get("summary", {}).get("revenue_at_risk_usd", 0.0))
            for item in analyses + journeys
        ]
        observed = sum(values)
        if not values:
            return (
                "Revenue and cost impact are unavailable because this session "
                "has no analyzed service or journey evidence.",
                [],
                [],
                "UNAVAILABLE",
            )
        return (
            f"The current session contains ${observed:,.2f} in explicitly "
            "reported or modeled value at risk. Review the cited receipts "
            "before treating the amount as an operational fact.",
            [item["receipt_id"] for item in analyses + journeys],
            ["lyte.journey_gap_value", "lyte.cost_per_success"],
            "MODELED",
        )

    if any(term in needle for term in ("service", "error budget", "burn", "slo")):
        candidates: list[tuple[float, str, str]] = []
        for item in analyses:
            for service in item.get("summary", {}).get("services", []):
                burn = service.get("burn_rate")
                if isinstance(burn, (int, float)):
                    candidates.append(
                        (
                            float(burn),
                            str(service.get("name") or "unnamed service"),
                            item["receipt_id"],
                        )
                    )
        if not candidates:
            return (
                "Service health is unavailable because no service analysis "
                "with an observed error-budget burn exists in this session.",
                [],
                [],
                "UNAVAILABLE",
            )
        burn, name, receipt_id = max(candidates)
        return (
            f"{name} has the highest observed error-budget burn at {burn:.3f}. "
            "This is a measured ranking, not a causal diagnosis.",
            [receipt_id],
            ["lyte.availability_sli", "lyte.error_budget_burn"],
            "MEASURED",
        )

    if any(term in needle for term in ("agent", "model", "token", "tool")):
        candidates = [
            item
            for item in analyses
            if item.get("summary", {}).get("agent_trace_count", 0)
        ]
        if not candidates:
            return (
                "AI-agent operations are unavailable because this session "
                "contains no agent trace summaries.",
                [],
                [],
                "UNAVAILABLE",
            )
        latest = candidates[0]
        count = int(latest["summary"].get("agent_trace_count", 0))
        success = latest["summary"].get("agent_success_rate")
        return (
            f"The latest analysis contains {count} agent traces with a "
            f"success rate of {success}. Prompt and response content were "
            "not retained.",
            [latest["receipt_id"]],
            ["szl.lambda_advisory"],
            "MEASURED",
        )

    if any(term in needle for term in ("journey", "checkout", "conversion")):
        if not journeys:
            return (
                "Journey evidence is unavailable in this session.",
                [],
                [],
                "UNAVAILABLE",
            )
        latest = journeys[0]
        summary = latest.get("summary", {})
        return (
            f"{summary.get('name', 'The latest journey')} is "
            f"{summary.get('status', 'UNAVAILABLE')} with "
            f"${float(summary.get('revenue_at_risk_usd', 0.0)):,.2f} "
            "in modeled value at risk.",
            [latest["receipt_id"]],
            ["lyte.journey_gap_value", "szl.lambda_advisory"],
            "MODELED",
        )

    if any(term in needle for term in ("source", "github", "deployment")):
        if not sources:
            return (
                "No external source receipt is available in this session.",
                [],
                [],
                "UNAVAILABLE",
            )
        latest = sources[0]
        repository = latest.get("summary", {}).get("repository", "the source")
        return (
            f"The most recent fixed-source observation is {repository}. "
            "Its receipt preserves source and payload identity.",
            [latest["receipt_id"]],
            [],
            "REPORTED",
        )

    if not records:
        return (
            "No session evidence is available. Connect a source or run the "
            "enterprise scenario before asking for an evidence-backed answer.",
            [],
            [],
            "UNAVAILABLE",
        )

    return (
        "The session contains evidence, but the question is outside the "
        "deterministic Lyte answer catalog. Review the cited receipts or ask "
        "about service health, journeys, business impact, agents, or sources.",
        [str(item["receipt_id"]) for item in records[:5]],
        [],
        "MEASURED",
    )


def answer_question(
    question: str,
    records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Answer from session evidence without an external model or invented facts."""
    cleaned = " ".join(str(question).split())
    if not cleaned:
        raise ValueError("question must not be blank")
    answer, receipts, formulas, truth = _analysis_answer(cleaned, records)
    return {
        "schema": "szl.ask-lyte/v3",
        "question": cleaned,
        "answer": answer,
        "truth_label": truth,
        "confidence": (
            0.92
            if truth in {"MEASURED", "REPORTED"}
            else 0.72
            if truth == "MODELED"
            else 0.0
        ),
        "evidence_receipt_ids": receipts,
        "formula_ids": formulas,
        "causality_claimed": False,
        "limitations": [
            "Answers are restricted to records in the caller's hashed session.",
            "Correlation is not represented as causation.",
            "No external language model is required or trusted as an evidence source.",
        ],
        "recommended_next_review": (
            "Inspect the cited receipt chain."
            if receipts
            else "Connect or analyze a source-backed observation."
        ),
        "effectors_enabled": False,
    }


LEDGER = SessionLedger()

__all__ = [
    "LEDGER",
    "SessionLedger",
    "answer_question",
    "observation_receipt",
    "session_scope",
]
