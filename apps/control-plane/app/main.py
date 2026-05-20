"""FastAPI application entry point with lifespan, routers, OTel, and error handlers."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.auth.jwt_validator import warm_jwks_cache
from app.auth.obo_exchange import init_obo_client
from app.config import get_settings
from app.exceptions import ControlPlaneError
from app.middleware.audit import AuditMiddleware
from app.routers import healthz, sessions, users

logger = logging.getLogger(__name__)

# ── Structured JSON logging setup ─────────────────────────────────────────────
import json
import logging


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_obj = {
            "ts": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Merge any extra fields attached by audit middleware
        for key, val in record.__dict__.items():
            if key not in (
                "args", "asctime", "created", "exc_info", "exc_text", "filename",
                "funcName", "id", "levelname", "levelno", "lineno", "module",
                "msecs", "message", "msg", "name", "pathname", "process",
                "processName", "relativeCreated", "stack_info", "thread", "threadName",
            ):
                log_obj[key] = val
        if record.exc_info:
            log_obj["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(log_obj)


def _configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(_JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup and shutdown lifecycle."""
    _configure_logging()
    settings = get_settings()

    # 1. Configure Azure Monitor / OpenTelemetry
    if settings.appinsights_connection_string:
        try:
            from azure.monitor.opentelemetry import configure_azure_monitor
            configure_azure_monitor(
                connection_string=settings.appinsights_connection_string,
            )
            logger.info("Azure Monitor OpenTelemetry configured")
        except Exception as exc:
            logger.warning("Azure Monitor OTel setup failed (non-fatal): %s", exc)

    # 2. Pre-warm JWKS cache (stampede-safe)
    try:
        await warm_jwks_cache()
        logger.info("JWKS cache warmed on startup")
    except Exception as exc:
        logger.error("JWKS warm-up failed (startup will continue): %s", exc)

    # 3. Fetch control-plane client secret from Key Vault and init MSAL OBO client
    try:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient

        credential = DefaultAzureCredential()
        kv_client = SecretClient(vault_url=settings.key_vault_uri, credential=credential)
        secret = kv_client.get_secret(settings.api_app_client_secret_kv_ref)
        await init_obo_client(client_secret=secret.value)
        logger.info("OBO client initialized from Key Vault secret")
    except Exception as exc:
        logger.error("OBO client init failed (auth will be unavailable): %s", exc)

    logger.info("Control plane startup complete")
    yield

    logger.info("Control plane shutting down")


# ── App factory ───────────────────────────────────────────────────────────────

def create_app() -> FastAPI:
    app = FastAPI(
        title="OpenSandbox Control Plane",
        version="0.1.0",
        description="FastAPI control plane for OpenSandbox on Azure",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # ── OpenTelemetry FastAPI instrumentation ─────────────────────────────────
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        FastAPIInstrumentor.instrument_app(app)
    except Exception as exc:
        logger.warning("FastAPI OTel instrumentation failed (non-fatal): %s", exc)

    # ── Middleware ────────────────────────────────────────────────────────────
    app.add_middleware(AuditMiddleware)

    # ── Routers ───────────────────────────────────────────────────────────────
    app.include_router(healthz.router)
    app.include_router(sessions.router)
    app.include_router(users.router)

    # ── Global exception handlers ─────────────────────────────────────────────
    @app.exception_handler(ControlPlaneError)
    async def control_plane_error_handler(
        request: Request, exc: ControlPlaneError
    ) -> JSONResponse:
        logger.warning(
            "ControlPlaneError: %s detail=%s path=%s",
            exc.message, exc.detail, request.url.path,
        )
        body = {
            "error": exc.error_code,
            "message": exc.message,
        }
        if exc.detail:
            body["detail"] = exc.detail

        headers = {}
        if exc.http_status == 503:
            headers["Retry-After"] = "90"

        return JSONResponse(status_code=exc.http_status, content=body, headers=headers)

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        logger.exception("Unhandled exception for %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"error": "INTERNAL_ERROR", "message": "An unexpected error occurred"},
        )

    return app


app = create_app()
