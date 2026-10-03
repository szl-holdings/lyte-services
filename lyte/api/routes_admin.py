"""Authenticated, fail-closed production scope provisioning."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from lyte.api.dependencies import get_runtime
from lyte.api.models import ProvisionScopeRequest
from lyte.domain import TenantSpec, WorkspaceSpec
from lyte.governance import (
    AuthenticationError,
    AuthenticationUnavailable,
    Principal,
)
from lyte.persistence import ProvisioningConflict

router = APIRouter(prefix="/api/lyte/v2/admin", tags=["administration"])


def _admin_principal(
    request: Request,
    authorization: str | None = Header(default=None),
) -> Principal:
    runtime = get_runtime(request)
    if runtime.authentication is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="authentication is not configured; request denied",
        )
    try:
        principal = runtime.authentication.authenticate_header(authorization)
    except AuthenticationUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="authentication verifier is unavailable; request denied",
        ) from exc
    except AuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="bearer authentication failed",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    if "admin" not in principal.roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="verified administrator authority is required",
        )
    return principal


AdminPrincipal = Annotated[Principal, Depends(_admin_principal)]


@router.post("/scopes")
def provision_scope(
    payload: ProvisionScopeRequest,
    request: Request,
    principal: AdminPrincipal,
) -> JSONResponse:
    """Create one exact tenant/workspace scope without seeding product data."""

    runtime = get_runtime(request)
    if runtime.demo_mode:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="scope provisioning is unavailable in SAMPLE mode",
        )
    tenant_id = UUID(payload.tenant_id)
    workspace_id = UUID(payload.workspace_id)
    if principal.tenant_id != tenant_id or workspace_id not in principal.workspace_ids:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="verified administrator is not authorized for the requested scope",
        )

    try:
        schema = runtime.database.schema_readiness(
            require_migration_revision=runtime.settings.is_production
        )
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="database schema verification is unavailable; request denied",
        ) from exc
    if not schema.ready:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="database schema is not ready; apply the required migrations",
        )

    tenant = TenantSpec(payload.tenant_slug, payload.tenant_name, tenant_id)
    workspace = WorkspaceSpec(
        tenant_id,
        payload.workspace_slug,
        payload.workspace_name,
        workspace_id,
    )
    try:
        result = runtime.store.provision_scope(tenant, workspace)
    except ProvisioningConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="scope identity conflicts with an existing provisioned resource",
        ) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="scope provisioning could not be durably completed",
        ) from exc

    body: dict[str, Any] = {
        "schema": "szl.lyte.scope-provisioning/v1",
        "state": result.state,
        "scope": result.scope.to_dict(),
        "tenant_created": result.tenant_created,
        "workspace_created": result.workspace_created,
        "stream_kinds_initialized": True,
        "receipt_hash": result.receipt_hash,
        "receipt_signature_state": "UNAVAILABLE",
        "sample_data_seeded": False,
        "data_mode": "REAL",
        "effectors_enabled": False,
        "human_approval_required": True,
        "truth_label": "MEASURED",
    }
    return JSONResponse(
        body,
        status_code=(
            status.HTTP_201_CREATED if result.state == "CREATED" else status.HTTP_200_OK
        ),
    )


__all__ = ["router"]
