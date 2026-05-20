"""Tests for JWT validation: JWKS caching, audience check, expiry, signature."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import pytest
from freezegun import freeze_time
from jose import jwt

from app.auth.jwt_validator import (
    _get_lock,
    get_jwks,
    validate_token,
    warm_jwks_cache,
)
from app.exceptions import InvalidTokenError, TokenExpiredError, WrongAudienceError

# ── Fixtures ──────────────────────────────────────────────────────────────────

TENANT_ID = "test-tenant-id"
API_APP_ID = "test-api-app-id"
AKS_SERVER_APP_ID = "test-aks-server-app-id"

# Generate an RSA key pair for tests using cryptography
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
import json
import base64


def _int_to_base64url(n: int) -> str:
    length = (n.bit_length() + 7) // 8
    b = n.to_bytes(length, "big")
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


@pytest.fixture(scope="module")
def rsa_keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()
    pub_numbers = public_key.public_key().public_numbers() if hasattr(public_key, "public_key") else public_key.public_numbers()
    return private_key, pub_numbers


@pytest.fixture(scope="module")
def mock_jwks(rsa_keypair):
    _, pub_numbers = rsa_keypair
    return {
        "keys": [
            {
                "kty": "RSA",
                "use": "sig",
                "kid": "test-kid-1",
                "n": _int_to_base64url(pub_numbers.n),
                "e": _int_to_base64url(pub_numbers.e),
                "alg": "RS256",
            }
        ]
    }


@pytest.fixture
def mock_settings(monkeypatch):
    from app import config
    settings = config.Settings(
        tenant_id=TENANT_ID,
        api_app_id=API_APP_ID,
        aks_server_app_id=AKS_SERVER_APP_ID,
        aks_api_fqdn="aks.example.com",
        key_vault_uri="https://kv-test.vault.azure.net/",
        acr_fqdn="acr.azurecr.io",
        api_app_client_secret_kv_ref="cp-secret",
    )
    monkeypatch.setattr(config, "_settings", settings)
    return settings


def _make_token(
    private_key,
    *,
    aud: str = API_APP_ID,
    iss: str | None = None,
    exp_offset: int = 3600,
    kid: str = "test-kid-1",
) -> str:
    from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption
    pem = private_key.private_bytes(Encoding.PEM, PrivateFormat.TraditionalOpenSSL, NoEncryption())
    now = int(time.time())
    payload = {
        "oid": "user-oid-123",
        "upn": "user@example.com",
        "tid": TENANT_ID,
        "aud": aud,
        "iss": iss or f"https://sts.windows.net/{TENANT_ID}/",
        "iat": now,
        "nbf": now,
        "exp": now + exp_offset,
        "scp": "Sandbox.Use",
    }
    return jwt.encode(payload, pem, algorithm="RS256", headers={"kid": kid})


# ── Tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_validate_token_success(rsa_keypair, mock_jwks, mock_settings):
    """Valid token signed by mock JWKS returns claims."""
    private_key, _ = rsa_keypair

    with patch("app.auth.jwt_validator._jwks_cache", mock_jwks), \
         patch("app.auth.jwt_validator._jwks_fetched_at", time.monotonic()):
        token = _make_token(private_key)
        claims = await validate_token(token)

    assert claims["oid"] == "user-oid-123"
    assert claims["upn"] == "user@example.com"


@pytest.mark.asyncio
async def test_validate_token_wrong_audience(rsa_keypair, mock_jwks, mock_settings):
    """Token with wrong audience raises WrongAudienceError (plan AC #4)."""
    private_key, _ = rsa_keypair

    with patch("app.auth.jwt_validator._jwks_cache", mock_jwks), \
         patch("app.auth.jwt_validator._jwks_fetched_at", time.monotonic()):
        token = _make_token(private_key, aud="https://management.azure.com/")
        with pytest.raises(WrongAudienceError) as exc_info:
            await validate_token(token)

    assert exc_info.value.error_code == "WRONG_AUDIENCE"
    assert exc_info.value.http_status == 401


@pytest.mark.asyncio
async def test_validate_token_expired(rsa_keypair, mock_jwks, mock_settings):
    """Expired token raises TokenExpiredError."""
    private_key, _ = rsa_keypair

    with patch("app.auth.jwt_validator._jwks_cache", mock_jwks), \
         patch("app.auth.jwt_validator._jwks_fetched_at", time.monotonic()):
        token = _make_token(private_key, exp_offset=-100)  # already expired
        with pytest.raises(TokenExpiredError):
            await validate_token(token)


@pytest.mark.asyncio
async def test_validate_token_wrong_issuer(rsa_keypair, mock_jwks, mock_settings):
    """Token with wrong issuer raises InvalidTokenError."""
    private_key, _ = rsa_keypair

    with patch("app.auth.jwt_validator._jwks_cache", mock_jwks), \
         patch("app.auth.jwt_validator._jwks_fetched_at", time.monotonic()):
        token = _make_token(private_key, iss="https://sts.windows.net/other-tenant/")
        with pytest.raises(InvalidTokenError):
            await validate_token(token)


@pytest.mark.asyncio
async def test_jwks_cache_is_used(mock_settings):
    """JWKS fetch is only called once within TTL window."""
    import app.auth.jwt_validator as jv

    # Reset cache
    jv._jwks_cache = None
    jv._jwks_fetched_at = 0.0

    fake_jwks = {"keys": [{"kid": "x", "kty": "RSA"}]}
    fetch_mock = AsyncMock(return_value=fake_jwks)

    with patch("app.auth.jwt_validator._fetch_jwks", fetch_mock):
        result1 = await get_jwks()
        result2 = await get_jwks()

    # Should only have been called once
    assert fetch_mock.call_count == 1
    assert result1 == result2 == fake_jwks


@pytest.mark.asyncio
async def test_jwks_cache_refreshes_after_ttl(mock_settings):
    """JWKS is re-fetched after TTL expires."""
    import app.auth.jwt_validator as jv

    jv._jwks_cache = {"keys": [{"kid": "old"}]}
    jv._jwks_fetched_at = 0.0  # force stale

    fresh_jwks = {"keys": [{"kid": "new"}]}
    fetch_mock = AsyncMock(return_value=fresh_jwks)

    with patch("app.auth.jwt_validator._fetch_jwks", fetch_mock):
        result = await get_jwks()

    assert fetch_mock.call_count == 1
    assert result == fresh_jwks
