"""Request-scope dependencies for authenticated and public-sample paths."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import Header, HTTPException, Request, status

from lyte.domain import Scope
from lyte.governance import (
    AuthenticationError,
    AuthenticationUnavailable,
    AuthorizationError,
)

_MUTATION_ROLES = frozenset({"operator", "admin"})


def get_runtime(request: Request) -> Any:
    """Return the initialized runtime without importing the app module."""

    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="runtime is unavailable",
        )
    return runtime


def _parse_scope(tenant_id: str | None, workspace_id: str | None) -> Scope | None:
    if tenant_id is None and workspace_id is None:
        return None
    if not tenant_id or not workspace_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Lyte-Tenant-ID and X-Lyte-Workspace-ID must be supplied together",
        )
    try:
        return Scope(UUID(tenant_id), UUID(workspace_id))
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="tenant and workspace identifiers must be UUIDs",
        ) from exc


def _authenticated_scope(
    request: Request,
    *,
    authorization: str | None,
    tenant_id: str | None,
    workspace_id: str | None,
    required_roles: frozenset[str] | None,
) -> Scope:
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

    scope = _parse_scope(tenant_id, workspace_id)
    if scope is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="an explicit tenant and workspace scope is required",
        )
    try:
        runtime.authentication.authorize_workspace(
            principal,
            scope,
            required_roles=required_roles,
        )
    except AuthorizationError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="the verified principal is not authorized for this scope",
        ) from exc
    return scope


def get_read_scope(
    request: Request,
    authorization: str | None = Header(default=None),
    tenant_id: str | None = Header(default=None, alias="X-Lyte-Tenant-ID"),
    workspace_id: str | None = Header(default=None, alias="X-Lyte-Workspace-ID"),
) -> Scope:
    """Allow the fixed public SAMPLE scope; authenticate every other scope."""

    runtime = get_runtime(request)
    if runtime.demo_mode and authorization is None and tenant_id is None and workspace_id is None:
        return runtime.demo_scope
    return _authenticated_scope(
        request,
        authorization=authorization,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        required_roles=None,
    )


def get_mutation_scope(
    request: Request,
    authorization: str | None = Header(default=None),
    tenant_id: str | None = Header(default=None, alias="X-Lyte-Tenant-ID"),
    workspace_id: str | None = Header(default=None, alias="X-Lyte-Workspace-ID"),
) -> Scope:
    """Require verified operator/admin authority for every persisted mutation."""

    return _authenticated_scope(
        request,
        authorization=authorization,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        required_roles=_MUTATION_ROLES,
    )


__all__ = ["get_mutation_scope", "get_read_scope", "get_runtime"]
