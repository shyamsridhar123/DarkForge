"""
Tests for SandboxClient — uses respx to mock the HTTPX transport.

Assertions:
  - create_session sends Authorization: Bearer <token>
  - credential.get_token is called with the correct scope
  - 401 → AuthenticationError
  - 403 → AuthorizationError
  - 429 → RateLimitError
  - 503 + Retry-After → PropagationTimeoutError
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
import respx
import httpx

from opensandbox_azure import SandboxClient
from opensandbox_azure.exceptions import (
    AuthenticationError,
    AuthorizationError,
    PropagationTimeoutError,
    RateLimitError,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

API_URL = "https://api-opensandbox.example.com"
SCOPE = "api://my-app-id/.default"
FAKE_TOKEN = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.fake"

_SESSION_PAYLOAD = {
    "session_id": "sess-abc123",
    "image": "acr.example.azurecr.io/sandbox/base/python:3.12",
    "state": "running",
    "identity_tier": "user_bound",
    "created_at": datetime.now(timezone.utc).isoformat(),
    "low_latency": False,
}

_RUN_PAYLOAD = {
    "session_id": "sess-abc123",
    "command": "echo hello",
    "stdout": "hello\n",
    "stderr": "",
    "exit_code": 0,
    "duration_ms": 42,
    "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
}


def _make_credential(token: str = FAKE_TOKEN) -> MagicMock:
    """Return a mock TokenCredential that returns *token*."""
    cred = MagicMock()
    access_token = MagicMock()
    access_token.token = token
    cred.get_token.return_value = access_token
    return cred


def _make_client(credential: MagicMock | None = None) -> SandboxClient:
    return SandboxClient(
        api_url=API_URL,
        credential=credential or _make_credential(),
        scope=SCOPE,
    )


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------

class TestConstructor:
    def test_requires_scope(self) -> None:
        with pytest.raises(ValueError, match="scope is required"):
            SandboxClient(api_url=API_URL, credential=_make_credential(), scope=None)


# ---------------------------------------------------------------------------
# Authorization header + credential.get_token
# ---------------------------------------------------------------------------

class TestAuthHeader:
    @respx.mock
    def test_create_session_sends_bearer_token(self) -> None:
        cred = _make_credential()
        client = _make_client(cred)

        route = respx.post(f"{API_URL}/sessions").mock(
            return_value=httpx.Response(201, json=_SESSION_PAYLOAD)
        )
        client.create_session(image="acr.example.azurecr.io/sandbox/base/python:3.12")

        assert route.called
        req = route.calls.last.request
        assert req.headers["Authorization"] == f"Bearer {FAKE_TOKEN}"

    @respx.mock
    def test_get_token_called_with_correct_scope(self) -> None:
        cred = _make_credential()
        client = _make_client(cred)

        respx.post(f"{API_URL}/sessions").mock(
            return_value=httpx.Response(201, json=_SESSION_PAYLOAD)
        )
        client.create_session(image="acr.example.azurecr.io/sandbox/base/python:3.12")

        cred.get_token.assert_called_once_with(SCOPE)

    @respx.mock
    def test_traceparent_header_is_w3c_format(self) -> None:
        cred = _make_credential()
        client = _make_client(cred)

        route = respx.post(f"{API_URL}/sessions").mock(
            return_value=httpx.Response(201, json=_SESSION_PAYLOAD)
        )
        client.create_session(image="acr.example.azurecr.io/sandbox/base/python:3.12")

        req = route.calls.last.request
        tp = req.headers.get("traceparent", "")
        # 00-<32hex>-<16hex>-<2hex>
        assert re.match(r"^00-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$", tp), tp


# ---------------------------------------------------------------------------
# HTTP error mapping
# ---------------------------------------------------------------------------

class TestErrorMapping:
    @respx.mock
    def test_401_raises_authentication_error(self) -> None:
        client = _make_client()
        respx.post(f"{API_URL}/sessions").mock(
            return_value=httpx.Response(401, text="Unauthorized")
        )
        with pytest.raises(AuthenticationError) as exc_info:
            client.create_session(image="img")
        assert exc_info.value.status_code == 401

    @respx.mock
    def test_403_raises_authorization_error(self) -> None:
        client = _make_client()
        respx.post(f"{API_URL}/sessions").mock(
            return_value=httpx.Response(403, text="Forbidden")
        )
        with pytest.raises(AuthorizationError) as exc_info:
            client.create_session(image="img")
        assert exc_info.value.status_code == 403

    @respx.mock
    def test_429_raises_rate_limit_error(self) -> None:
        client = _make_client()
        respx.post(f"{API_URL}/sessions").mock(
            return_value=httpx.Response(
                429, text="Too Many Requests", headers={"Retry-After": "30"}
            )
        )
        with pytest.raises(RateLimitError) as exc_info:
            client.create_session(image="img")
        assert exc_info.value.status_code == 429
        assert exc_info.value.retry_after == 30

    @respx.mock
    def test_503_raises_propagation_timeout_error(self) -> None:
        client = _make_client()
        respx.post(f"{API_URL}/sessions").mock(
            return_value=httpx.Response(
                503,
                text="Workload identity not yet propagated",
                headers={"Retry-After": "5"},
            )
        )
        with pytest.raises(PropagationTimeoutError) as exc_info:
            client.create_session(image="img")
        assert exc_info.value.status_code == 503
        assert exc_info.value.retry_after == 5

    @respx.mock
    def test_503_without_retry_after(self) -> None:
        client = _make_client()
        respx.post(f"{API_URL}/sessions").mock(
            return_value=httpx.Response(503, text="Service Unavailable")
        )
        with pytest.raises(PropagationTimeoutError) as exc_info:
            client.create_session(image="img")
        assert exc_info.value.retry_after is None


# ---------------------------------------------------------------------------
# Happy-path smoke tests
# ---------------------------------------------------------------------------

class TestHappyPath:
    @respx.mock
    def test_create_session_returns_handle(self) -> None:
        client = _make_client()
        respx.post(f"{API_URL}/sessions").mock(
            return_value=httpx.Response(201, json=_SESSION_PAYLOAD)
        )
        sess = client.create_session(image="acr.example.azurecr.io/sandbox/base/python:3.12")
        assert sess.session_id == "sess-abc123"
        assert sess.state.value == "running"

    @respx.mock
    def test_run_returns_result(self) -> None:
        client = _make_client()
        respx.post(f"{API_URL}/sessions").mock(
            return_value=httpx.Response(201, json=_SESSION_PAYLOAD)
        )
        respx.post(f"{API_URL}/sessions/sess-abc123/run").mock(
            return_value=httpx.Response(200, json=_RUN_PAYLOAD)
        )
        sess = client.create_session(image="acr.example.azurecr.io/sandbox/base/python:3.12")
        result = sess.run("echo hello")
        assert result.stdout == "hello\n"
        assert result.exit_code == 0

    @respx.mock
    def test_list_sessions(self) -> None:
        client = _make_client()
        respx.get(f"{API_URL}/sessions").mock(
            return_value=httpx.Response(200, json=[_SESSION_PAYLOAD])
        )
        sessions = client.list_sessions()
        assert len(sessions) == 1
        assert sessions[0].session_id == "sess-abc123"

    @respx.mock
    def test_get_session(self) -> None:
        client = _make_client()
        respx.get(f"{API_URL}/sessions/sess-abc123").mock(
            return_value=httpx.Response(200, json=_SESSION_PAYLOAD)
        )
        sess = client.get_session("sess-abc123")
        assert sess.session_id == "sess-abc123"

    @respx.mock
    def test_delete_session(self) -> None:
        client = _make_client()
        respx.post(f"{API_URL}/sessions").mock(
            return_value=httpx.Response(201, json=_SESSION_PAYLOAD)
        )
        respx.delete(f"{API_URL}/sessions/sess-abc123").mock(
            return_value=httpx.Response(204)
        )
        sess = client.create_session(image="acr.example.azurecr.io/sandbox/base/python:3.12")
        sess.delete()  # should not raise


# ---------------------------------------------------------------------------
# Async tests
# ---------------------------------------------------------------------------

class TestAsync:
    @respx.mock
    @pytest.mark.asyncio
    async def test_acreate_session_sends_bearer_token(self) -> None:
        cred = _make_credential()
        client = _make_client(cred)

        route = respx.post(f"{API_URL}/sessions").mock(
            return_value=httpx.Response(201, json=_SESSION_PAYLOAD)
        )
        await client.acreate_session(image="acr.example.azurecr.io/sandbox/base/python:3.12")

        req = route.calls.last.request
        assert req.headers["Authorization"] == f"Bearer {FAKE_TOKEN}"
