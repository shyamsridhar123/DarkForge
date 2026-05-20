"""Integration tests for the sessions router using httpx.AsyncClient + mocked AKS."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

TENANT_ID = "test-tenant-id"
API_APP_ID = "test-api-app-id"
AKS_SERVER_APP_ID = "test-aks-server-app-id"


# ── Settings fixture (must be first so app initializes with test config) ──────

@pytest.fixture(autouse=True)
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
        curated_images=["python:3.12", "node:20"],
    )
    monkeypatch.setattr(config, "_settings", settings)
    return settings


# ── Token helpers ─────────────────────────────────────────────────────────────

from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
from jose import jwt
import base64


def _int_to_b64url(n: int) -> str:
    length = (n.bit_length() + 7) // 8
    b = n.to_bytes(length, "big")
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


@pytest.fixture(scope="module")
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def mock_jwks(rsa_key):
    pub = rsa_key.public_key().public_numbers()
    return {
        "keys": [{"kty": "RSA", "use": "sig", "kid": "k1", "alg": "RS256",
                  "n": _int_to_b64url(pub.n), "e": _int_to_b64url(pub.e)}]
    }


def _token(rsa_key, *, oid: str = "user-oid-1", roles: list[str] | None = None,
           aud: str = API_APP_ID, exp_offset: int = 3600) -> str:
    pem = rsa_key.private_bytes(Encoding.PEM, PrivateFormat.TraditionalOpenSSL, NoEncryption())
    now = int(time.time())
    return jwt.encode({
        "oid": oid, "upn": f"{oid}@example.com", "tid": TENANT_ID,
        "aud": aud, "iss": f"https://sts.windows.net/{TENANT_ID}/",
        "iat": now, "nbf": now, "exp": now + exp_offset,
        "scp": "Sandbox.Use", "roles": roles or [],
    }, pem, algorithm="RS256", headers={"kid": "k1"})


# ── App fixture ───────────────────────────────────────────────────────────────

@pytest.fixture
def app(mock_jwks):
    """Return the FastAPI app with JWKS + OBO mocked out."""
    import app.auth.jwt_validator as jv
    import app.auth.obo_exchange as obo
    import app.routers.sessions as sr

    # Pre-populate JWKS cache so validate_token works without network
    jv._jwks_cache = mock_jwks
    jv._jwks_fetched_at = time.monotonic()

    # Mock OBO exchange ��� return a dummy AKS token
    msal_mock = MagicMock()
    msal_mock.acquire_token_on_behalf_of.return_value = {
        "access_token": "aks-token-stub",
        "expires_in": 3600,
    }
    obo._msal_app = msal_mock
    obo._token_cache.clear()

    # Clear session store between tests
    sr._sessions.clear()

    from app.main import app as fastapi_app
    return fastapi_app


@pytest.fixture
def mock_aks(monkeypatch):
    """Mock AKS client calls to avoid real Kubernetes API calls."""
    import app.aks_client as aks

    create_mock = AsyncMock(return_value={
        "session_id": "sess-abc123",
        "pod_name": "sandbox-abc123",
        "namespace": "ns-user-oid-1",
        "identity_tier": "per-user",
        "connection_info": {"pod": "sandbox-abc123", "namespace": "ns-user-oid-1"},
    })
    exec_mock = AsyncMock(return_value={"stdout": "hello", "stderr": "", "exit_code": 0, "session_id": "sess-abc123"})
    delete_mock = AsyncMock(return_value=None)
    get_mock = AsyncMock(return_value={"name": "sandbox-abc123", "phase": "Running"})
    obo_mock = AsyncMock(return_value="aks-token-stub")

    monkeypatch.setattr(aks, "create_sandbox_pod", create_mock)
    monkeypatch.setattr(aks, "exec_in_sandbox", exec_mock)
    monkeypatch.setattr(aks, "delete_sandbox_pod", delete_mock)
    monkeypatch.setattr(aks, "get_sandbox_pod", get_mock)
    monkeypatch.setattr("app.routers.sessions.create_sandbox_pod", create_mock)
    monkeypatch.setattr("app.routers.sessions.exec_in_sandbox", exec_mock)
    monkeypatch.setattr("app.routers.sessions.delete_sandbox_pod", delete_mock)
    monkeypatch.setattr("app.routers.sessions.get_sandbox_pod", get_mock)
    monkeypatch.setattr("app.auth.obo_exchange.exchange_for_aks", obo_mock)

    return {"create": create_mock, "exec": exec_mock, "delete": delete_mock}


# ── Tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_missing_auth_returns_401(app):
    """Request without Authorization header → 401."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/sessions", json={"image": "python:3.12"})
    assert response.status_code == 422  # FastAPI rejects missing required header at validation layer
    # For auth-missing specifically, FastAPI returns 422 for missing Header field


@pytest.mark.asyncio
async def test_invalid_token_returns_401(app):
    """Garbage token → 401."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/sessions",
            json={"image": "python:3.12"},
            headers={"Authorization": "Bearer not.a.real.token"},
        )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_wrong_audience_returns_401(app, rsa_key):
    """Token with wrong audience → 401 with WRONG_AUDIENCE error code (plan AC #4)."""
    bad_token = _token(rsa_key, aud="https://management.azure.com/")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/sessions",
            json={"image": "python:3.12"},
            headers={"Authorization": f"Bearer {bad_token}"},
        )
    assert response.status_code == 401
    assert response.json()["error"] == "WRONG_AUDIENCE"


@pytest.mark.asyncio
async def test_low_latency_without_role_returns_403(app, rsa_key, mock_aks):
    """low_latency=True without Sandbox.LowLatency role → 403."""
    token = _token(rsa_key, roles=[])  # no low-latency role
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/sessions",
            json={"image": "python:3.12", "low_latency": True},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 403
    assert response.json()["error"] == "INSUFFICIENT_SCOPE"


@pytest.mark.asyncio
async def test_low_latency_with_role_succeeds(app, rsa_key, mock_aks):
    """low_latency=True WITH Sandbox.LowLatency role → 201."""
    token = _token(rsa_key, roles=["Sandbox.LowLatency"])
    mock_aks["create"].return_value["identity_tier"] = "shared_warm_pool"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/sessions",
            json={"image": "python:3.12", "low_latency": True},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 201
    assert response.json()["identity_tier"] == "shared_warm_pool"


@pytest.mark.asyncio
async def test_create_session_success(app, rsa_key, mock_aks):
    """Valid token + valid image → 201 with session_id."""
    token = _token(rsa_key)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/sessions",
            json={"image": "python:3.12"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 201
    body = response.json()
    assert "session_id" in body
    assert "connection_info" in body


@pytest.mark.asyncio
async def test_cross_user_get_returns_403(app, rsa_key, mock_aks):
    """User A creates a session; User B tries to GET it → 403."""
    import app.routers.sessions as sr

    # Pre-seed a session owned by user-oid-1
    sr._sessions["sess-owned-by-1"] = {
        "session_id": "sess-owned-by-1",
        "pod_name": "sandbox-x",
        "namespace": "ns-user-oid-1",
        "identity_tier": "per-user",
        "owner_oid": "user-oid-1",
        "image": "acr.azurecr.io/python:3.12",
        "status": "running",
        "connection_info": {},
    }

    # User 2 tries to access it
    token_user2 = _token(rsa_key, oid="user-oid-2")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/sessions/sess-owned-by-1",
            headers={"Authorization": f"Bearer {token_user2}"},
        )
    assert response.status_code == 403
    assert response.json()["error"] == "FORBIDDEN_NOT_OWNER"


@pytest.mark.asyncio
async def test_image_not_in_catalog_returns_422(app, rsa_key, mock_aks):
    """Image not in curated catalog → 422."""
    token = _token(rsa_key)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/sessions",
            json={"image": "evil/image:latest"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 422
    assert response.json()["error"] == "IMAGE_NOT_ALLOWED"
