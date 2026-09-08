"""Explicit developer auth and fail-closed OIDC/JWT verification."""

from __future__ import annotations

import hmac
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID

from lyte.domain import Scope
from lyte.settings import Environment, Settings

RBAC_ROLES = frozenset({"admin", "operator", "analyst", "viewer", "auditor"})


class AuthenticationError(RuntimeError):
    """Credentials were absent, malformed, expired, or unauthorized."""


class AuthenticationUnavailable(AuthenticationError):
    """The configured verifier cannot safely verify credentials."""


class AuthorizationError(RuntimeError):
    """A verified principal does not own the requested scope."""


@dataclass(frozen=True, slots=True)
class Principal:
    subject: str
    tenant_id: UUID
    workspace_ids: frozenset[UUID]
    roles: frozenset[str] = frozenset()
    issuer: str = ""
    authentication_method: str = "oidc"
    claims: Mapping[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        subject = self.subject.strip()
        if not subject or len(subject) > 256:
            raise AuthenticationError("verified subject is invalid")
        if not self.workspace_ids:
            raise AuthenticationError("verified principal has no workspace scope")
        object.__setattr__(self, "subject", subject)
        object.__setattr__(self, "tenant_id", UUID(str(self.tenant_id)))
        object.__setattr__(
            self,
            "workspace_ids",
            frozenset(UUID(str(item)) for item in self.workspace_ids),
        )
        roles = frozenset(str(item).strip() for item in self.roles if str(item).strip())
        unsupported = roles - RBAC_ROLES
        if unsupported:
            raise AuthenticationError(
                "verified principal contains unsupported role(s): " + ", ".join(sorted(unsupported))
            )
        object.__setattr__(self, "roles", roles)
        object.__setattr__(self, "claims", dict(self.claims))


class TokenVerifier(Protocol):
    def verify(self, token: str) -> Principal: ...


class OIDCJWTVerifier:
    """Verify signed JWTs through the issuer JWKS endpoint.

    PyJWT is imported lazily so an image missing the crypto verifier fails
    closed at authentication time instead of accepting unverified claims.
    """

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        jwks_url: str,
        algorithms: Sequence[str] = ("RS256", "ES256"),
        tenant_claim: str = "tenant_id",
        workspaces_claim: str = "workspace_ids",
        roles_claim: str = "roles",
    ) -> None:
        if not issuer or not audience or not jwks_url:
            raise AuthenticationUnavailable("complete OIDC settings are required")
        self.issuer = issuer
        self.audience = audience
        self.jwks_url = jwks_url
        self.algorithms = tuple(algorithms)
        if not self.algorithms:
            raise AuthenticationUnavailable("at least one JWT algorithm is required")
        self.tenant_claim = tenant_claim
        self.workspaces_claim = workspaces_claim
        self.roles_claim = roles_claim
        self._jwks_client: Any | None = None

    def _client(self) -> Any:
        try:
            from jwt import PyJWKClient
        except ImportError as exc:
            raise AuthenticationUnavailable(
                "PyJWT with cryptographic support is required for OIDC"
            ) from exc
        if self._jwks_client is None:
            self._jwks_client = PyJWKClient(self.jwks_url, cache_keys=True)
        return self._jwks_client

    def verify(self, token: str) -> Principal:
        if not isinstance(token, str) or not 16 <= len(token) <= 16_384:
            raise AuthenticationError("bearer credential is malformed")
        try:
            import jwt
        except ImportError as exc:
            raise AuthenticationUnavailable(
                "PyJWT with cryptographic support is required for OIDC"
            ) from exc
        try:
            signing_key = self._client().get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=list(self.algorithms),
                audience=self.audience,
                issuer=self.issuer,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except AuthenticationUnavailable:
            raise
        except Exception as exc:  # PyJWT exposes several version-specific errors.
            raise AuthenticationError("bearer credential verification failed") from exc
        return _principal_from_claims(
            claims,
            issuer=self.issuer,
            method="oidc",
            tenant_claim=self.tenant_claim,
            workspaces_claim=self.workspaces_claim,
            roles_claim=self.roles_claim,
        )


class DevTokenVerifier:
    """One explicitly configured opaque token for local development only."""

    def __init__(self, settings: Settings) -> None:
        if settings.environment is Environment.PRODUCTION or not settings.dev_auth_enabled:
            raise AuthenticationUnavailable("developer authentication is not enabled")
        if not settings.dev_auth_token:
            raise AuthenticationUnavailable("developer token is missing")
        self._token = settings.dev_auth_token
        self._principal = Principal(
            subject=settings.dev_auth_subject,
            tenant_id=UUID(str(settings.dev_tenant_id)),
            workspace_ids=frozenset({UUID(str(settings.dev_workspace_id))}),
            roles=frozenset({"admin", "operator"}),
            issuer="local-explicit-dev-auth",
            authentication_method="development_token",
            claims={},
        )

    def verify(self, token: str) -> Principal:
        if not isinstance(token, str) or not hmac.compare_digest(token, self._token):
            raise AuthenticationError("bearer credential verification failed")
        return self._principal


def _strings(value: Any, *, claim: str) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)) and all(isinstance(item, str) for item in value):
        return list(value)
    raise AuthenticationError(f"verified {claim} claim has an invalid shape")


def _principal_from_claims(
    claims: Mapping[str, Any],
    *,
    issuer: str,
    method: str,
    tenant_claim: str,
    workspaces_claim: str,
    roles_claim: str,
) -> Principal:
    try:
        tenant_id = UUID(str(claims[tenant_claim]))
        workspace_ids = frozenset(
            UUID(value) for value in _strings(claims[workspaces_claim], claim=workspaces_claim)
        )
        roles_value = claims.get(roles_claim, [])
        roles = frozenset(_strings(roles_value, claim=roles_claim)) if roles_value else frozenset()
        subject = str(claims["sub"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AuthenticationError("verified JWT is missing a valid scope claim") from exc
    return Principal(
        subject=subject,
        tenant_id=tenant_id,
        workspace_ids=workspace_ids,
        roles=roles,
        issuer=issuer,
        authentication_method=method,
        claims=claims,
    )


class AuthenticationService:
    def __init__(self, verifier: TokenVerifier) -> None:
        self.verifier = verifier

    @classmethod
    def from_settings(cls, settings: Settings) -> AuthenticationService:
        if settings.dev_auth_enabled:
            return cls(DevTokenVerifier(settings))
        if settings.oidc_issuer and settings.oidc_audience and settings.oidc_jwks_url:
            return cls(
                OIDCJWTVerifier(
                    issuer=settings.oidc_issuer,
                    audience=settings.oidc_audience,
                    jwks_url=settings.oidc_jwks_url,
                )
            )
        raise AuthenticationUnavailable(
            "no authentication verifier is configured; anonymous access is denied"
        )

    def authenticate_header(self, authorization: str | None) -> Principal:
        if not authorization:
            raise AuthenticationError("Bearer authorization is required")
        scheme, separator, token = authorization.partition(" ")
        if separator != " " or scheme.lower() != "bearer" or not token.strip():
            raise AuthenticationError("Bearer authorization is required")
        return self.verifier.verify(token.strip())

    @staticmethod
    def authorize_workspace(
        principal: Principal,
        scope: Scope,
        *,
        required_roles: frozenset[str] | None = None,
    ) -> None:
        if principal.tenant_id != scope.tenant_id:
            raise AuthorizationError("principal does not own the requested tenant")
        if scope.workspace_id not in principal.workspace_ids:
            raise AuthorizationError("principal does not own the requested workspace")
        if required_roles and principal.roles.isdisjoint(required_roles):
            raise AuthorizationError("principal lacks the required role")
