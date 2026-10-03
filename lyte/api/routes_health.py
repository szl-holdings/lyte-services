"""Liveness, readiness, source identity, and metrics routes."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST

from lyte import __version__
from lyte.build_receipt import BuildReceiptObservation, observe_build_receipt

router = APIRouter()
_SHA = re.compile(r"^[0-9a-f]{40}$")
_ROOT = Path(__file__).resolve().parents[2]
_REQUIRED_PUBLIC_ROUTES = frozenset({"/readyz", "/api/live", "/build-receipt.json"})


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
    receipt = observe_build_receipt(
        _ROOT,
        expected_source_revision=revision,
        required=getattr(runtime, "require_build_receipt", False),
    )
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
        "build_receipt": receipt.to_dict(),
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
    payload, status, _ = _readiness_contract(request, force_build_receipt=False)
    return JSONResponse(payload, status_code=status)


def _unavailable_schema_contract(runtime: Any) -> dict[str, Any]:
    return {
        "ready": False,
        "state": "UNAVAILABLE",
        "expected_revisions": [],
        "observed_revisions": [],
        "missing_tables": [],
        "validation_mode": (
            "ALEMBIC_EXACT_HEAD" if runtime.settings.is_production else "LOCAL_SCHEMA"
        ),
        "truth_label": "UNAVAILABLE",
    }


def _public_route_state(request: Request) -> tuple[str, list[str]]:
    paths = request.app.openapi().get("paths", {})
    observed = {path for path, operations in paths.items() if "get" in operations}
    missing = sorted(_REQUIRED_PUBLIC_ROUTES - observed)
    return ("READY" if not missing else "MISSING_REQUIRED_ROUTES"), missing


def _readiness_contract(
    request: Request,
    *,
    force_build_receipt: bool,
) -> tuple[dict[str, Any], int, BuildReceiptObservation]:
    """Measure one shared source, payload, route, database, and schema contract."""

    runtime = request.app.state.runtime
    checks: dict[str, str] = {"configuration": "READY"}
    ready = True
    schema_contract: dict[str, Any]
    try:
        schema = runtime.database.schema_readiness(
            require_migration_revision=runtime.settings.is_production
        )
        runtime.database.ping()
        checks["database"] = "READY"
        runtime.metrics.set_db_pool_healthy(True)
        checks["schema"] = schema.state
        schema_contract = schema.to_dict()
        if not schema.ready:
            ready = False
    except Exception:  # readiness must not expose driver/server details
        checks["database"] = "UNAVAILABLE"
        checks["schema"] = "UNAVAILABLE"
        schema_contract = _unavailable_schema_contract(runtime)
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

    route_state, missing_routes = _public_route_state(request)
    checks["public_routes"] = route_state
    if missing_routes:
        ready = False

    receipt_required = getattr(runtime, "require_build_receipt", False) or force_build_receipt
    receipt = observe_build_receipt(
        _ROOT,
        expected_source_revision=source["revision"],
        required=receipt_required,
    )
    if receipt.valid:
        checks["build_receipt"] = "READY"
    elif receipt.state == "MISSING_OPTIONAL_LOCAL" and not receipt_required:
        checks["build_receipt"] = receipt.state
    else:
        checks["build_receipt"] = receipt.state
        ready = False

    status = 200 if ready else 503
    return (
        {
            "ready": ready,
            "service": "lyte-signal-lattice",
            "version": __version__,
            **runtime_identity(source),
            "checks": checks,
            "database_schema": schema_contract,
            "build": {
                "state": source["state"],
                "revision": source["revision"],
            },
            "source_binding": {
                "bindings_agree": source["bindings_agree"],
                "required": runtime.require_source_binding,
            },
            "public_routes": {
                "required": sorted(_REQUIRED_PUBLIC_ROUTES),
                "missing": missing_routes,
            },
            "build_receipt": receipt.to_dict(),
            "truth_label": "MEASURED" if ready else "UNAVAILABLE",
        },
        status,
        receipt,
    )


@router.get("/api/live", tags=["operations"])
def live(request: Request) -> Response:
    readiness, status, receipt = _readiness_contract(request, force_build_receipt=True)
    runtime = request.app.state.runtime
    payload = {
        "schema": "szl.lyte-live/v1",
        "service": "lyte-signal-lattice",
        "version": __version__,
        **runtime_identity(source_identity()),
        "observation_state": "VERIFIED" if status == 200 else "UNAVAILABLE",
        "ready": readiness["ready"],
        "checks": readiness["checks"],
        "database_schema": readiness["database_schema"],
        "source_binding": readiness["source_binding"],
        "public_routes": readiness["public_routes"],
        "build_receipt": receipt.to_dict(),
        "persistence": runtime.persistence_contract(),
        "data_mode": "SAMPLE" if runtime.demo_mode else "REAL_ONLY",
        "truth_label": "MEASURED" if status == 200 else "UNAVAILABLE",
    }
    return JSONResponse(payload, status_code=status)


@router.get("/build-receipt.json", tags=["operations"])
def build_receipt(request: Request) -> Response:
    source = source_identity()
    receipt = observe_build_receipt(
        _ROOT,
        expected_source_revision=source["revision"],
        required=True,
    )
    if receipt.valid and receipt.raw is not None:
        return Response(
            receipt.raw,
            media_type="application/json",
            headers={"Cache-Control": "no-store"},
        )
    return JSONResponse(
        {
            "schema": "szl.lyte-build-receipt-error/v1",
            "service": "lyte-signal-lattice",
            **runtime_identity(source),
            "build_receipt": receipt.to_dict(),
            "truth_label": "UNAVAILABLE",
        },
        status_code=503,
        headers={"Cache-Control": "no-store"},
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
