"""Health and readiness endpoints — no auth required."""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.auth.jwt_validator import _jwks_cache
from app.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


@router.get("/healthz", include_in_schema=False)
async def healthz() -> JSONResponse:
    """Liveness probe — always 200 if the process is running."""
    return JSONResponse({"status": "ok"})


@router.get("/readyz", include_in_schema=False)
async def readyz() -> JSONResponse:
    """
    Readiness probe — checks:
      1. JWKS cache populated
      2. AKS API reachable (HEAD against the API FQDN)
      3. Key Vault reachable
    """
    settings = get_settings()
    checks: dict[str, str] = {}
    healthy = True

    # 1. JWKS cache
    if _jwks_cache is not None and len(_jwks_cache.get("keys", [])) > 0:
        checks["jwks_cache"] = "ok"
    else:
        checks["jwks_cache"] = "not_populated"
        healthy = False

    # 2. AKS API reachable
    try:
        async with httpx.AsyncClient(timeout=3.0, verify=False) as client:
            r = await client.get(f"https://{settings.aks_api_fqdn}/healthz")
            checks["aks_api"] = "ok" if r.status_code < 500 else f"http_{r.status_code}"
            if r.status_code >= 500:
                healthy = False
    except Exception as exc:
        checks["aks_api"] = f"unreachable: {exc}"
        healthy = False

    # 3. Key Vault reachable (unauthenticated ping — 401 is fine, means KV is up)
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(settings.key_vault_uri)
            checks["key_vault"] = "ok" if r.status_code in (200, 401, 403) else f"http_{r.status_code}"
    except Exception as exc:
        checks["key_vault"] = f"unreachable: {exc}"
        healthy = False

    status_code = 200 if healthy else 503
    return JSONResponse({"status": "ok" if healthy else "degraded", "checks": checks}, status_code=status_code)
