"""Liveness, readiness, source identity, and metrics routes."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST
from sqlalchemy import text

from lyte import __version__

router = APIRouter()
_SHA = re.compile(r"^[0-9a-f]{40}$")
_ROOT = Path(__file__).resolve().parents[2]


def source_identity() -> dict[str, Any]:
    candidates: dict[str, str] = {}
    invalid_sources: list[str] = []
    for name in ("LYTE_SOURCE_REVISION", "SOURCE_REVISION", "GITHUB_SHA"):
        value = os.getenv(name, "").strip().lower()
        if _SHA.fullmatch(value):
            candidates[name] = value
        elif value and value not in {"unknown", "unavailable", "unbound"}:
            invalid_sources.append(name)
    marker = _ROOT / "source_revision.txt"
    if marker.is_file():
        value = marker.read_text(encoding="utf-8").strip().lower()
        if _SHA.fullmatch(value):
            candidates["source_revision.txt"] = value
        elif value and value not in {"unknown", "unavailable", "unbound"}:
            invalid_sources.append("source_revision.txt")
    revisions = sorted(set(candidates.values()))
    if invalid_sources or len(revisions) > 1:
        state = "MISMATCH"
        revision = None
    elif len(revisions) == 1:
        state = "OBSERVED"
        revision = revisions[0]
    else:
        state = "UNBOUND"
        revision = None
    return {
        "state": state,
        "revision": revision,
        "bindings_agree": state == "OBSERVED",
        "evidence_sources": sorted(candidates),
        "invalid_sources": sorted(invalid_sources),
        "candidate_count": len(candidates),
        "distinct_revision_count": len(revisions),
    }


def source_revision() -> str | None:
    return source_identity()["revision"]


def runtime_identity(source: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the one fail-closed identity tuple shared by operational routes."""

    observation = source or source_identity()
    revision = observation["revision"] if observation["bindings_agree"] else None
    return {
        "source_repository": "szl-holdings/lyte-services",
        "source_revision": revision,
        "runtime_repository": "szl-holdings/lyte-services",
        "runtime_source_revision": revision,
        "effectors_enabled": False,
        "human_approval_required": True,
    }


def build_contract(request: Request) -> dict[str, Any]:
    source = source_identity()
    revision = source["revision"]
    runtime = request.app.state.runtime
    persistence = runtime.persistence_contract()
    return {
        "schema": "szl.lyte-build/v2",
        "service": "lyte-signal-lattice",
        "version": __version__,
        **runtime_identity(source),
        "build": {
            "state": source["state"],
            "revision": revision,
            "repository": "szl-holdings/lyte-services",
        },
        "source_binding": {
            "bindings_agree": source["bindings_agree"],
            "product_repository": "szl-holdings/lyte-services",
            "product_revision": revision,
            "hub_surface": "SZLHOLDINGS/lyte",
            "evidence_sources": source["evidence_sources"],
            "invalid_sources": source["invalid_sources"],
            "distinct_revision_count": source["distinct_revision_count"],
        },
        "persistence": persistence,
        "data_mode": "SAMPLE" if runtime.demo_mode else "REAL_ONLY",
        "truth_label": "MEASURED" if source["bindings_agree"] else "UNAVAILABLE",
    }


@router.get("/healthz", tags=["operations"])
def healthz(request: Request) -> dict[str, Any]:
    source = source_identity()
    return {
        "ok": True,
        "service": "lyte-signal-lattice",
        "version": __version__,
        **runtime_identity(source),
        "truth_label": "MEASURED",
        "request_id": getattr(request.state, "request_id", None),
    }


@router.get("/readyz", tags=["operations"])
def readyz(request: Request) -> Response:
    runtime = request.app.state.runtime
    checks: dict[str, str] = {"configuration": "READY"}
    ready = True
    try:
        with runtime.database.session() as session:
            session.execute(text("SELECT 1"))
        checks["database"] = "READY"
        runtime.metrics.set_db_pool_healthy(True)
    except Exception:  # readiness must not expose driver/server details
        checks["database"] = "UNAVAILABLE"
        runtime.metrics.set_db_pool_healthy(False)
        ready = False
    source = source_identity()
    if source["bindings_agree"]:
        checks["source_binding"] = "READY"
    elif runtime.require_source_binding:
        checks["source_binding"] = source["state"]
        ready = False
    else:
        checks["source_binding"] = f"{source['state']}_OPTIONAL_LOCAL"
    status = 200 if ready else 503
    return JSONResponse(
        {
            "ready": ready,
            "service": "lyte-signal-lattice",
            "version": __version__,
            **runtime_identity(source),
            "checks": checks,
            "build": {
                "state": source["state"],
                "revision": source["revision"],
            },
            "source_binding": {
                "bindings_agree": source["bindings_agree"],
                "required": runtime.require_source_binding,
            },
            "truth_label": "MEASURED" if ready else "UNAVAILABLE",
        },
        status_code=status,
    )


@router.get("/api/build-info", tags=["operations"])
@router.get("/api/source", tags=["operations"])
@router.get("/.well-known/szl-source.json", tags=["operations"])
def source_info(request: Request) -> dict[str, Any]:
    return build_contract(request)


@router.get("/metrics", include_in_schema=False)
@router.get("/api/lyte/v2/metrics", include_in_schema=False)
def metrics(request: Request) -> Response:
    """Expose the same real registry through local and application-owned paths.

    Hosted ingress can reserve /metrics for its own infrastructure. Public
    application attestation uses /api/lyte/v2/metrics instead, without sending
    infrastructure credentials or replacing empty responses with success.
    Neither route performs ingestion, creates receipts, or grants authority.
    """
    runtime = request.app.state.runtime
    return Response(runtime.metrics.render(), media_type=CONTENT_TYPE_LATEST)
