"""Tests for OBO exchange — asserts AKS scope, NOT management.azure.com (plan S-C8 / B1)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.auth import obo_exchange
from app.exceptions import OBOExchangeError

TENANT_ID = "test-tenant-id"
API_APP_ID = "test-api-app-id"
AKS_SERVER_APP_ID = "test-aks-server-app-id"
EXPECTED_SCOPE = f"{AKS_SERVER_APP_ID}/.default"

# ── Fixtures ──────────────────────────────────────────────────────────────────

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
    )
    monkeypatch.setattr(config, "_settings", settings)
    return settings


@pytest.fixture(autouse=True)
def reset_obo_state():
    """Reset module-level MSAL app and cache between tests."""
    obo_exchange._msal_app = None
    obo_exchange._token_cache.clear()
    yield
    obo_exchange._msal_app = None
    obo_exchange._token_cache.clear()


def _make_mock_msal_app(token: str = "aks-access-token-xyz", expires_in: int = 3600):
    mock_app = MagicMock()
    mock_app.acquire_token_on_behalf_of.return_value = {
        "access_token": token,
        "expires_in": expires_in,
        "token_type": "Bearer",
    }
    return mock_app


# ── Tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_obo_uses_aks_server_app_scope():
    """
    CRITICAL: OBO exchange MUST use AKS_SERVER_APP_ID/.default scope.
    NOT management.azure.com (plan S-C8, Architect B1 fix).
    """
    mock_app = _make_mock_msal_app()
    obo_exchange._msal_app = mock_app

    token = await obo_exchange.exchange_for_aks(
        user_token="user-bearer-token",
        user_oid="user-oid-123",
    )

    # Assert the scope passed to MSAL
    call_kwargs = mock_app.acquire_token_on_behalf_of.call_args
    scopes_used = call_kwargs.kwargs.get("scopes") or call_kwargs.args[1] if call_kwargs.args else call_kwargs.kwargs["scopes"]

    assert EXPECTED_SCOPE in scopes_used, (
        f"OBO scope must be {EXPECTED_SCOPE!r}, got {scopes_used!r}. "
        "This is a critical security requirement (plan S-C8 / B1)."
    )
    assert "management.azure.com" not in str(scopes_used), (
        "OBO scope must NOT contain management.azure.com — this is the AKS server app ID scope."
    )
    assert token == "aks-access-token-xyz"


@pytest.mark.asyncio
async def test_obo_caches_token():
    """OBO token is cached; MSAL is only called once for the same user+scope."""
    mock_app = _make_mock_msal_app()
    obo_exchange._msal_app = mock_app

    t1 = await obo_exchange.exchange_for_aks("user-token", "oid-abc")
    t2 = await obo_exchange.exchange_for_aks("user-token", "oid-abc")

    assert t1 == t2
    assert mock_app.acquire_token_on_behalf_of.call_count == 1


@pytest.mark.asyncio
async def test_obo_different_users_get_separate_tokens():
    """Each user OID gets its own OBO exchange."""
    mock_app = _make_mock_msal_app()
    obo_exchange._msal_app = mock_app

    await obo_exchange.exchange_for_aks("token-a", "oid-111")
    await obo_exchange.exchange_for_aks("token-b", "oid-222")

    assert mock_app.acquire_token_on_behalf_of.call_count == 2


@pytest.mark.asyncio
async def test_obo_surfaces_aadsts_error():
    """AADSTS errors are raised as OBOExchangeError — not swallowed."""
    mock_app = MagicMock()
    mock_app.acquire_token_on_behalf_of.return_value = {
        "error": "invalid_grant",
        "error_description": "AADSTS65001: The user has not consented to use the application.",
        "correlation_id": "test-corr-id",
    }
    obo_exchange._msal_app = mock_app

    with pytest.raises(OBOExchangeError) as exc_info:
        await obo_exchange.exchange_for_aks("bad-token", "oid-xyz")

    assert "invalid_grant" in exc_info.value.message
    assert exc_info.value.http_status == 502


@pytest.mark.asyncio
async def test_init_obo_client_sets_msal_app():
    """init_obo_client initializes the MSAL app with correct parameters."""
    with patch("msal.ConfidentialClientApplication") as mock_cls:
        mock_cls.return_value = MagicMock()
        await obo_exchange.init_obo_client("super-secret")

    mock_cls.assert_called_once()
    call_kwargs = mock_cls.call_args
    assert call_kwargs.kwargs.get("client_id") == API_APP_ID or call_kwargs.args[0] == API_APP_ID
    assert TENANT_ID in str(call_kwargs)
