"""Environment-backed settings with fail-closed production validation."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from urllib.parse import urlsplit


class ConfigurationError(RuntimeError):
    """The process configuration cannot satisfy its declared environment."""


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"

    @classmethod
    def parse(cls, value: str | None) -> Environment:
        normalized = (value or cls.DEVELOPMENT.value).strip().lower()
        aliases = {"dev": cls.DEVELOPMENT, "prod": cls.PRODUCTION}
        if normalized in aliases:
            return aliases[normalized]
        try:
            return cls(normalized)
        except ValueError as exc:
            allowed = ", ".join(item.value for item in cls)
            raise ConfigurationError(f"LYTE_ENV must be one of: {allowed}") from exc


def _boolean(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError("boolean settings must be true/false or 1/0")


def _normalize_database_url(value: str) -> str:
    url = value.strip()
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url.removeprefix("postgres://")
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url.removeprefix("postgresql://")
    return url


def _require_https(name: str, value: str | None) -> None:
    if not value:
        raise ConfigurationError(f"{name} is required in production")
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ConfigurationError(f"{name} must be an HTTPS URL without user info")


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime settings.

    Development conveniences are opt-in. Production cannot start with SQLite,
    developer authentication, or an incomplete OIDC verifier configuration.
    Secret-bearing fields are excluded from ``repr``.
    """

    environment: Environment = Environment.DEVELOPMENT
    database_url: str = field(default="sqlite+pysqlite:///./lyte.db", repr=False)
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_jwks_url: str | None = None
    dev_auth_enabled: bool = False
    dev_auth_token: str | None = field(default=None, repr=False)
    webhook_hmac_secret: str | None = field(default=None, repr=False)
    dev_auth_subject: str = "local-developer"
    dev_tenant_id: str | None = None
    dev_workspace_id: str | None = None

    def __post_init__(self) -> None:
        environment = (
            self.environment
            if isinstance(self.environment, Environment)
            else Environment.parse(str(self.environment))
        )
        database_url = _normalize_database_url(self.database_url)
        object.__setattr__(self, "environment", environment)
        object.__setattr__(self, "database_url", database_url)
        if not database_url:
            raise ConfigurationError("DATABASE_URL cannot be empty")
        scheme = database_url.split(":", 1)[0].lower()
        if scheme not in {"sqlite", "sqlite+pysqlite", "postgresql", "postgresql+psycopg"}:
            raise ConfigurationError("DATABASE_URL must use SQLite or PostgreSQL")
        if self.dev_auth_enabled:
            if environment is Environment.PRODUCTION:
                raise ConfigurationError("developer authentication is forbidden in production")
            token = self.dev_auth_token or ""
            if len(token) < 32:
                raise ConfigurationError("LYTE_DEV_AUTH_TOKEN must contain at least 32 characters")
            if not self.dev_tenant_id or not self.dev_workspace_id:
                raise ConfigurationError(
                    "developer authentication requires LYTE_DEV_TENANT_ID and LYTE_DEV_WORKSPACE_ID"
                )
        if self.webhook_hmac_secret is not None and len(self.webhook_hmac_secret) < 32:
            raise ConfigurationError(
                "LYTE_WEBHOOK_HMAC_SECRET must contain at least 32 characters when set"
            )
        if environment is Environment.PRODUCTION:
            if scheme.startswith("sqlite"):
                raise ConfigurationError("production requires a PostgreSQL DATABASE_URL")
            _require_https("OIDC_ISSUER", self.oidc_issuer)
            _require_https("OIDC_JWKS_URL", self.oidc_jwks_url)
            if not (self.oidc_audience or "").strip():
                raise ConfigurationError("OIDC_AUDIENCE is required in production")

    @property
    def is_production(self) -> bool:
        return self.environment is Environment.PRODUCTION

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        values = os.environ if environ is None else environ
        environment = Environment.parse(values.get("LYTE_ENV"))
        default_database = (
            "sqlite+pysqlite:///:memory:"
            if environment is Environment.TEST
            else "sqlite+pysqlite:///./lyte.db"
        )
        return cls(
            environment=environment,
            database_url=_normalize_database_url(values.get("DATABASE_URL", default_database)),
            oidc_issuer=values.get("OIDC_ISSUER"),
            oidc_audience=values.get("OIDC_AUDIENCE"),
            oidc_jwks_url=values.get("OIDC_JWKS_URL"),
            dev_auth_enabled=_boolean(values.get("LYTE_DEV_AUTH_ENABLED")),
            dev_auth_token=values.get("LYTE_DEV_AUTH_TOKEN"),
            webhook_hmac_secret=values.get("LYTE_WEBHOOK_HMAC_SECRET"),
            dev_auth_subject=values.get("LYTE_DEV_AUTH_SUBJECT", "local-developer"),
            dev_tenant_id=values.get("LYTE_DEV_TENANT_ID"),
            dev_workspace_id=values.get("LYTE_DEV_WORKSPACE_ID"),
        )
