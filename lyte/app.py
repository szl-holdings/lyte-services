"""Lyte Enterprise FastAPI application."""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from opentelemetry import trace
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from lyte import __version__
from lyte.api.routes_analysis import router as analysis_router
from lyte.api.routes_ask import router as ask_router
from lyte.api.routes_catalog import router as catalog_router
from lyte.api.routes_entities import router as entities_router
from lyte.api.routes_hatun import router as hatun_router
from lyte.api.routes_health import router as health_router
from lyte.api.routes_ingest import router as ingest_router
from lyte.connectors import ReplayProtector
from lyte.demo import seed_demo
from lyte.domain import Scope, TenantSpec, WorkspaceSpec
from lyte.governance import AuthenticationService, AuthenticationUnavailable
from lyte.persistence import Database, LyteStore
from lyte.settings import ConfigurationError, Settings
from lyte.telemetry import LyteMetrics, TelemetryConfig, setup_telemetry

_ROOT = Path(__file__).resolve().parent
_UI = _ROOT / "ui"
_DEMO_TENANT = UUID("11111111-1111-4111-8111-111111111111")
_DEMO_WORKSPACE = UUID("22222222-2222-4222-8222-222222222222")
_LOG = logging.getLogger("lyte")


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} must be true or false")


@dataclass(slots=True)
class Runtime:
    settings: Settings
    database: Database
    store: LyteStore
    demo_mode: bool
    demo_scope: Scope
    authentication: AuthenticationService | None
    metrics: LyteMetrics
    replay_protector: ReplayProtector
    require_source_binding: bool
    demo_seed: dict[str, Any] | None = None

    @property
    def effectors_enabled(self) -> bool:
        """Return the immutable execution boundary for this release."""
        return False

    @property
    def authentication_state(self) -> str:
        return "CONFIGURED" if self.authentication else "UNAVAILABLE_FAIL_CLOSED"

    def persistence_contract(self) -> dict[str, Any]:
        dialect = self.database.engine.dialect.name
        try:
            with self.database.session() as session:
                session.execute(text("SELECT 1 FROM lyte_workspaces LIMIT 1"))
            state = "READY"
        except Exception:
            state = "UNAVAILABLE"
        return {
            "backend": dialect,
            "state": state,
            "durability": (
                "DEPLOYMENT_MANAGED" if dialect == "postgresql" else "FILE_OR_PROCESS_SCOPED"
            ),
            "tenant_workspace_scoped": True,
            "truth_label": "MEASURED" if state == "READY" else "UNAVAILABLE",
        }

    def connector_catalog(self) -> list[dict[str, Any]]:
        webhook_state = (
            "AVAILABLE_AUTH_REQUIRED"
            if self.settings.webhook_hmac_secret
            else "UNAVAILABLE_CONFIGURATION_REQUIRED"
        )
        return [
            {
                "id": "github_actions",
                "state": "AVAILABLE_UNCONFIGURED",
                "mode": "READ_ONLY",
                "truth_label": "REPORTED",
            },
            {
                "id": "otlp_json_subset",
                "state": "AVAILABLE_AUTH_REQUIRED",
                "mode": "INGEST",
                "truth_label": "REPORTED",
            },
            {
                "id": "governed_event",
                "state": webhook_state,
                "mode": "INGEST",
                "truth_label": "REPORTED" if self.settings.webhook_hmac_secret else "UNAVAILABLE",
            },
            {
                "id": "prometheus_read",
                "state": "ROADMAP",
                "mode": "READ_ONLY",
                "truth_label": "ROADMAP",
            },
        ]


def _ensure_demo_scope(runtime: Runtime) -> None:
    try:
        runtime.store.create_tenant(
            TenantSpec("sample-enterprise", "Sample Enterprise", _DEMO_TENANT)
        )
    except IntegrityError:
        pass
    try:
        runtime.store.create_workspace(
            WorkspaceSpec(
                _DEMO_TENANT,
                "checkout-command",
                "Checkout Command",
                _DEMO_WORKSPACE,
            )
        )
    except IntegrityError:
        pass


def _make_runtime(application: FastAPI) -> Runtime:
    settings = Settings.from_env()
    database = Database(settings)
    if not settings.is_production:
        database.create_schema()
    store = LyteStore(database)
    try:
        authentication = AuthenticationService.from_settings(settings)
    except AuthenticationUnavailable:
        authentication = None
    demo_mode = _env_bool("LYTE_DEMO_MODE", not settings.is_production)
    if settings.is_production and demo_mode:
        raise ConfigurationError(
            "LYTE_DEMO_MODE is forbidden in production; sample data cannot be implicit"
        )
    runtime = Runtime(
        settings=settings,
        database=database,
        store=store,
        demo_mode=demo_mode,
        demo_scope=Scope(_DEMO_TENANT, _DEMO_WORKSPACE),
        authentication=authentication,
        metrics=application.state.lyte_metrics,
        replay_protector=ReplayProtector(),
        require_source_binding=(
            settings.is_production or _env_bool("LYTE_REQUIRE_SOURCE_BINDING", False)
        ),
    )
    if runtime.demo_mode:
        _ensure_demo_scope(runtime)
        runtime.demo_seed = seed_demo(runtime.store, runtime.demo_scope)
    return runtime


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    runtime = _make_runtime(application)
    application.state.runtime = runtime
    try:
        yield
    finally:
        runtime.database.dispose()


def create_app() -> FastAPI:
    application = FastAPI(
        title="Lyte Enterprise Signal Lattice",
        version=__version__,
        description=(
            "Governed business observability across services, journeys, AI agents, "
            "delivery, economics, decisions, and evidence. Effectors are disabled."
        ),
        lifespan=lifespan,
    )

    @application.middleware("http")
    async def request_contract(request: Request, call_next: Any) -> Any:
        import secrets

        started = time.perf_counter()
        request_id = request.headers.get("x-request-id", "").strip()[:128]
        if not request_id:
            request_id = secrets.token_hex(16)
        request.state.request_id = request_id
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > 2_000_000:
                    application.state.lyte_metrics.record_rejection("body_overflow")
                    return JSONResponse(
                        {"detail": "request body exceeds 2000000 bytes", "request_id": request_id},
                        status_code=413,
                    )
            except ValueError:
                application.state.lyte_metrics.record_rejection("content_length")
                return JSONResponse(
                    {"detail": "invalid content-length", "request_id": request_id},
                    status_code=400,
                )
        if request.method in {"POST", "PUT", "PATCH"}:
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 2_000_000:
                    application.state.lyte_metrics.record_rejection("body_overflow")
                    return JSONResponse(
                        {"detail": "request body exceeds 2000000 bytes", "request_id": request_id},
                        status_code=413,
                    )
            request._body = bytes(body)  # noqa: SLF001 - preserve the bounded body downstream
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        span_context = trace.get_current_span().get_span_context()
        trace_id = f"{span_context.trace_id:032x}" if span_context.is_valid else "unavailable"
        if span_context.is_valid:
            response.headers["X-Trace-ID"] = trace_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        if request.url.path.startswith(("/api/", "/.well-known/")) or request.url.path in {
            "/healthz",
            "/readyz",
            "/metrics",
        }:
            response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; font-src 'self'; "
            "object-src 'none'; base-uri 'self'; "
            "frame-ancestors 'self' https://huggingface.co https://*.huggingface.co; "
            "form-action 'self'"
        )
        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        _LOG.info(
            json.dumps(
                {
                    "event": "http_request",
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": elapsed_ms,
                    "request_id": request_id,
                    "trace_id": trace_id,
                },
                separators=(",", ":"),
            )
        )
        return response

    application.include_router(health_router)
    application.include_router(catalog_router)
    application.include_router(entities_router)
    application.include_router(ask_router)
    application.include_router(hatun_router)
    application.include_router(analysis_router)
    application.include_router(ingest_router)
    application.mount("/static/lyte", StaticFiles(directory=_UI), name="lyte-static")

    @application.get("/", include_in_schema=False)
    def product() -> FileResponse:
        return FileResponse(_UI / "index.html", media_type="text/html")

    revision = os.getenv("LYTE_SOURCE_REVISION", "").strip().lower() or "unknown"
    setup_telemetry(
        application,
        config=TelemetryConfig(
            service_version=__version__,
            deployment_environment=os.getenv("LYTE_ENV", "development"),
            source_revision=revision,
        ),
        metrics=LyteMetrics(),
        expose_metrics=False,
    )

    return application


app = create_app()


def run() -> None:
    uvicorn.run(
        "lyte.app:app",
        host="0.0.0.0",  # noqa: S104 - container entrypoint accepts external traffic
        port=int(os.getenv("PORT", "7860")),
    )


if __name__ == "__main__":
    run()
