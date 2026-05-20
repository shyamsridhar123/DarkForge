"""Entra ID JWKS validation with 1-hour cache and stampede protection."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx
from jose import ExpiredSignatureError, JWTError, jwk, jwt
from jose.exceptions import JWKError

from app.config import get_settings
from app.exceptions import InvalidTokenError, TokenExpiredError, WrongAudienceError

logger = logging.getLogger(__name__)

# ── Module-level cache ────────────────────────────────────────────────────────

_jwks_cache: dict[str, Any] | None = None
_jwks_fetched_at: float = 0.0
_jwks_lock: asyncio.Lock | None = None  # lazily initialized (event loop may not exist at import)


def _get_lock() -> asyncio.Lock:
    global _jwks_lock
    if _jwks_lock is None:
        _jwks_lock = asyncio.Lock()
    return _jwks_lock


async def _fetch_jwks() -> dict[str, Any]:
    """Fetch JWKS from Entra discovery endpoint."""
    settings = get_settings()
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(settings.jwks_uri)
        response.raise_for_status()
        return response.json()  # type: ignore[no-any-return]


async def get_jwks() -> dict[str, Any]:
    """Return cached JWKS, refreshing if stale. Stampede-safe via asyncio.Lock."""
    global _jwks_cache, _jwks_fetched_at

    settings = get_settings()
    now = time.monotonic()

    # Fast path: cache is fresh, no lock needed
    if _jwks_cache is not None and (now - _jwks_fetched_at) < settings.jwks_cache_ttl_seconds:
        return _jwks_cache

    async with _get_lock():
        # Re-check under lock (another coroutine may have refreshed while we waited)
        now = time.monotonic()
        if _jwks_cache is not None and (now - _jwks_fetched_at) < settings.jwks_cache_ttl_seconds:
            return _jwks_cache

        logger.info("Refreshing JWKS cache from %s", settings.jwks_uri)
        _jwks_cache = await _fetch_jwks()
        _jwks_fetched_at = time.monotonic()
        logger.info(
            "JWKS cache refreshed, %d keys loaded",
            len(_jwks_cache.get("keys", [])),
        )
        return _jwks_cache


async def validate_token(token: str) -> dict[str, Any]:
    """
    Validate an Entra-issued JWT.

    Checks:
      - Signature against JWKS
      - iss == expected issuer (sts.windows.net/<tenant>/)
      - aud == API_APP_ID  (raises WrongAudienceError if mismatch — plan AC #4)
      - exp, nbf

    Returns decoded claims dict.
    Raises InvalidTokenError (or subclass) on any failure.
    """
    settings = get_settings()

    # ── 1. Decode header to find the key ID ───────────────────────────────────
    try:
        unverified_header = jwt.get_unverified_header(token)
    except JWTError as exc:
        raise InvalidTokenError(f"Malformed JWT header: {exc}") from exc

    kid = unverified_header.get("kid")

    # ── 2. Retrieve the matching public key ───────────────────────────────────
    jwks = await get_jwks()
    matching_keys = [k for k in jwks.get("keys", []) if k.get("kid") == kid]

    if not matching_keys:
        # Key not in cache — attempt one refresh (key rotation edge case)
        logger.warning("kid=%s not found in JWKS cache, forcing refresh", kid)
        global _jwks_fetched_at
        _jwks_fetched_at = 0.0  # invalidate so get_jwks() re-fetches
        jwks = await get_jwks()
        matching_keys = [k for k in jwks.get("keys", []) if k.get("kid") == kid]
        if not matching_keys:
            raise InvalidTokenError(f"No JWKS key found for kid={kid!r}")

    try:
        public_key = jwk.construct(matching_keys[0])
    except JWKError as exc:
        raise InvalidTokenError(f"Failed to construct JWK: {exc}") from exc

    # ── 3. Decode + verify signature, exp, nbf ────────────────────────────────
    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            # Audience check done manually below for richer error
            options={"verify_aud": False},
        )
    except ExpiredSignatureError as exc:
        raise TokenExpiredError("Token has expired") from exc
    except JWTError as exc:
        raise InvalidTokenError(f"JWT decode/verify failed: {exc}") from exc

    # ── 4. Audience check — must be OpenSandbox API app ID (plan S-C8 / AC #4) ─
    token_aud = claims.get("aud", "")
    expected_aud = settings.api_app_id
    # aud can be a string or a list
    aud_values = token_aud if isinstance(token_aud, list) else [token_aud]
    if expected_aud not in aud_values:
        raise WrongAudienceError(
            f"Token audience {aud_values!r} does not match expected {expected_aud!r}",
            detail="aud claim must be the OpenSandbox API app ID",
        )

    # ── 5. Issuer check ───────────────────────────────────────────────────────
    expected_iss = settings.issuer
    token_iss = claims.get("iss", "")
    if token_iss != expected_iss:
        raise InvalidTokenError(
            f"Token issuer {token_iss!r} does not match expected {expected_iss!r}"
        )

    logger.debug(
        "Token validated for oid=%s upn=%s",
        claims.get("oid"),
        claims.get("upn"),
    )
    return claims


async def warm_jwks_cache() -> None:
    """Pre-populate the JWKS cache. Called from app lifespan."""
    await get_jwks()
