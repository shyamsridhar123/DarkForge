"""Confidential-client OBO token exchange for AKS access.

Audience MUST be AKS server app ID, NOT management.azure.com (plan S-C8 / B1).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import msal

from app.config import get_settings
from app.exceptions import OBOExchangeError

logger = logging.getLogger(__name__)

# ── Token cache: keyed by (user_oid, scope), value: (token_str, expires_at_monotonic) ──
_token_cache: dict[tuple[str, str], tuple[str, float]] = {}
_cache_lock: asyncio.Lock | None = None

# Module-level MSAL app — initialized once on startup via init_obo_client()
_msal_app: msal.ConfidentialClientApplication | None = None


def _get_cache_lock() -> asyncio.Lock:
    global _cache_lock
    if _cache_lock is None:
        _cache_lock = asyncio.Lock()
    return _cache_lock


async def init_obo_client(client_secret: str) -> None:
    """Initialize the MSAL ConfidentialClientApplication. Call from app lifespan."""
    global _msal_app
    settings = get_settings()
    authority = f"https://login.microsoftonline.com/{settings.tenant_id}"

    # MSAL is synchronous; wrapping in thread executor keeps FastAPI async-safe
    loop = asyncio.get_running_loop()
    _msal_app = await loop.run_in_executor(
        None,
        lambda: msal.ConfidentialClientApplication(
            client_id=settings.api_app_id,
            client_credential=client_secret,
            authority=authority,
        ),
    )
    logger.info(
        "MSAL ConfidentialClientApplication initialized for tenant=%s app=%s",
        settings.tenant_id,
        settings.api_app_id,
    )


def _get_msal_app() -> msal.ConfidentialClientApplication:
    if _msal_app is None:
        raise RuntimeError("OBO client not initialized — call init_obo_client() first")
    return _msal_app


async def exchange_for_aks(user_token: str, user_oid: str) -> str:
    """
    Exchange a user's access token for an AKS-scoped token via OBO.

    Scope: <AKS_SERVER_APP_ID>/.default  (plan S-C8 — NOT management.azure.com)

    Returns the access token string.
    Raises OBOExchangeError on AADSTS failures.
    """
    settings = get_settings()
    scope = settings.aks_obo_scope  # e.g. "api://<aks-server-app-id>/.default"

    cache_key = (user_oid, scope)

    # ── Cache lookup (avoid re-exchange while token is fresh) ─────────────────
    async with _get_cache_lock():
        cached = _token_cache.get(cache_key)
        now = time.monotonic()
        if cached is not None:
            token_str, expires_at = cached
            if now < expires_at:
                logger.debug("OBO cache hit for oid=%s scope=%s", user_oid, scope)
                return token_str

        # ── Perform OBO exchange (sync MSAL → thread executor) ────────────────
        app = _get_msal_app()
        loop = asyncio.get_running_loop()

        result: dict[str, Any] = await loop.run_in_executor(
            None,
            lambda: app.acquire_token_on_behalf_of(
                user_assertion=user_token,
                scopes=[scope],
            ),
        )

        if "error" in result:
            error_code = result.get("error", "unknown")
            error_desc = result.get("error_description", "")
            logger.error(
                "OBO exchange failed: error=%s description=%s oid=%s scope=%s",
                error_code,
                error_desc,
                user_oid,
                scope,
            )
            raise OBOExchangeError(
                f"OBO exchange failed: {error_code}",
                detail=error_desc,
            )

        access_token: str = result["access_token"]
        expires_in: int = result.get("expires_in", 3600)

        # Cache until (now + expires_in - 300s buffer)
        ttl = max(0, expires_in - 300)
        _token_cache[cache_key] = (access_token, now + ttl)

        logger.info(
            "OBO token acquired for oid=%s scope=%s expires_in=%ds",
            user_oid,
            scope,
            expires_in,
        )
        return access_token


def clear_obo_cache() -> None:
    """Evict all cached OBO tokens (e.g. for testing)."""
    _token_cache.clear()
