"""Stable tenant/workspace identity contracts."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from uuid import UUID, uuid4

_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$")


def _uuid(value: UUID | str, *, name: str) -> UUID:
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"{name} must be a UUID") from exc


def validate_slug(value: str, *, name: str = "slug") -> str:
    normalized = value.strip().lower()
    if not _SLUG.fullmatch(normalized):
        raise ValueError(f"{name} must be 3-64 lowercase letters, digits, or internal hyphens")
    return normalized


@dataclass(frozen=True, slots=True)
class Scope:
    tenant_id: UUID
    workspace_id: UUID

    def __init__(self, tenant_id: UUID | str, workspace_id: UUID | str) -> None:
        object.__setattr__(self, "tenant_id", _uuid(tenant_id, name="tenant_id"))
        object.__setattr__(self, "workspace_id", _uuid(workspace_id, name="workspace_id"))

    def to_dict(self) -> dict[str, str]:
        return {
            "tenant_id": str(self.tenant_id),
            "workspace_id": str(self.workspace_id),
        }


@dataclass(frozen=True, slots=True)
class TenantSpec:
    slug: str
    name: str
    id: UUID = field(init=False)

    def __init__(
        self,
        slug: str,
        name: str,
        id: UUID | str | None = None,  # noqa: A002
    ) -> None:
        clean_name = " ".join(name.split())
        if not clean_name or len(clean_name) > 200:
            raise ValueError("tenant name must contain 1-200 characters")
        object.__setattr__(self, "slug", validate_slug(slug, name="tenant slug"))
        object.__setattr__(self, "name", clean_name)
        object.__setattr__(self, "id", uuid4() if id is None else _uuid(id, name="id"))


@dataclass(frozen=True, slots=True)
class WorkspaceSpec:
    tenant_id: UUID
    slug: str
    name: str
    id: UUID = field(init=False)

    def __init__(
        self,
        tenant_id: UUID | str,
        slug: str,
        name: str,
        id: UUID | str | None = None,  # noqa: A002
    ) -> None:
        clean_name = " ".join(name.split())
        if not clean_name or len(clean_name) > 200:
            raise ValueError("workspace name must contain 1-200 characters")
        object.__setattr__(self, "tenant_id", _uuid(tenant_id, name="tenant_id"))
        object.__setattr__(self, "slug", validate_slug(slug, name="workspace slug"))
        object.__setattr__(self, "name", clean_name)
        object.__setattr__(self, "id", uuid4() if id is None else _uuid(id, name="id"))
