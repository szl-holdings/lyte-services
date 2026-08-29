"""Fail-closed Decision Cell Compiler. Hash is tamper-EVIDENT, not a signature."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass

from .cells import ADMITTED, CELLS, Cell

BLOCKED = "BLOCKED"
ALLOW = "ALLOW"
GENESIS = "0" * 64


@dataclass(frozen=True)
class CompileReceipt:
    id: str
    ts: str
    organ: str
    action: str
    decision: str
    honesty_tier: str
    lambda_status: str
    energy: None
    signer: str
    cell: str
    bind: str
    prev_hash: str
    hash: str
    doctrine: str
    lock: str
    flagship: str
    note: str

    def as_dict(self) -> dict:
        return asdict(self)


def _sha256(payload: dict) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()


def compile_cell(cell_id: str, *, prev_hash: str = GENESIS, signal: str = "") -> CompileReceipt:
    """Admit Lyte. Refuse N1–N8 and unknown ids. Never fabricates LIVE."""
    key = (cell_id or "").strip()
    cell: Cell | None = CELLS.get(key) or CELLS.get(key.lower()) or CELLS.get(key.upper())
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    rid = str(uuid.uuid4())
    prev = prev_hash if isinstance(prev_hash, str) and len(prev_hash) == 64 else GENESIS

    if cell is None:
        decision, honesty, note = BLOCKED, "UNAVAILABLE", "Unknown cell. Fail closed."
        bind = "UNBOUND"
        organ = "skeleton"
        cid = key or "<empty>"
    elif not cell.admitted:
        decision, honesty, note = BLOCKED, cell.honesty, cell.note
        bind = cell.bind
        organ = cell.organ
        cid = cell.id
    else:
        decision, honesty, note = ALLOW, cell.honesty, cell.note
        bind = cell.bind
        organ = cell.organ
        cid = cell.id

    body = {
        "id": rid,
        "ts": ts,
        "organ": organ,
        "action": "compile",
        "decision": decision,
        "honesty_tier": honesty,
        "lambda_status": "Conjecture 1",
        "energy": None,
        "signer": "UNSIGNED-honest",
        "cell": cid,
        "bind": bind,
        "prev_hash": prev,
        "doctrine": "v11",
        "lock": "749/14/163",
        "flagship": "a11oy",
        "note": note,
        "signal_digest": _sha256({"signal": signal, "cell": cid}),
    }
    digest = _sha256(body)
    return CompileReceipt(**{k: body[k] for k in CompileReceipt.__dataclass_fields__ if k != "hash"}, hash=digest)
