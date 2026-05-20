"""
Main SandboxClient — synchronous and async.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from azure.core.credentials import TokenCredential
from azure.identity import DefaultAzureCredential

from ._tracing import effective_traceparent
from .exceptions import (
    AuthenticationError,
    AuthorizationError,
    PropagationTimeoutError,
    RateLimitError,
    SandboxError,
    SessionNotFoundError,
)
from .models import RunResult, Session

_DEFAULT_TIMEOUT = 30.0
_DEFAULT_RUN_TIMEOUT = 60.0


def _raise_for_status(response: httpx.Response, session_id: str | None = None) -> None:
    """Map HTTP error codes to typed SDK exceptions."""
    if response.is_success:
        return
    status = response.status_code
    body = response.text
    if status == 401:
        raise AuthenticationError(
            f"Authentication failed (HTTP 401): {body}", status_code=401
        )
    if status == 403:
        raise AuthorizationError(
            f"Authorization denied (HTTP 403): {body}", status_code=403
        )
    if status == 404:
        sid = session_id or "unknown"
        raise SessionNotFoundError(sid)
    if status == 429:
        retry_after = _parse_retry_after(response)
        raise RateLimitError(
            f"Rate limit exceeded (HTTP 429): {body}", retry_after=retry_after
        )
    if status == 503:
        retry_after = _parse_retry_after(response)
        raise PropagationTimeoutError(
            f"Workload-identity propagation timeout (HTTP 503): {body}",
            retry_after=retry_after,
        )
    raise SandboxError(f"Unexpected HTTP {status}: {body}", status_code=status)


def _parse_retry_after(response: httpx.Response) -> int | None:
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _auth_headers(token: str, traceparent: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "traceparent": traceparent,
    }


class _SessionHandle:
    """Thin wrapper giving a Session model its ``run`` and ``delete`` methods.

    The handle holds a back-reference to the client so subsequent calls
    reuse the same credential/token-cache.
    """

    def __init__(self, client: "SandboxClient", data: Session) -> None:
        self._client = client
        self._data = data

    # ------------------------------------------------------------------ #
    # Proxy model attributes so callers can do: sess.session_id, sess.state …
    # ------------------------------------------------------------------ #
    def __getattr__(self, name: str) -> Any:
        return getattr(self._data, name)

    # ------------------------------------------------------------------ #
    # Sync API
    # ------------------------------------------------------------------ #
    def run(self, command: str, timeout_s: float = _DEFAULT_RUN_TIMEOUT) -> RunResult:
        """Execute *command* inside this session and return the result."""
        return self._client._run(self._data.session_id, command, timeout_s)

    def delete(self) -> None:
        """Terminate and delete this session."""
        self._client._delete_session(self._data.session_id)

    # ------------------------------------------------------------------ #
    # Async API
    # ------------------------------------------------------------------ #
    async def arun(
        self, command: str, timeout_s: float = _DEFAULT_RUN_TIMEOUT
    ) -> RunResult:
        return await self._client._arun(self._data.session_id, command, timeout_s)

    async def adelete(self) -> None:
        await self._client._adelete_session(self._data.session_id)


class SandboxClient:
    """Client for the OpenSandbox Azure control-plane API.

    Parameters
    ----------
    api_url:
        Base URL of the control-plane API (e.g. ``https://api-opensandbox.example.com``).
        Do **not** include a trailing slash.
    credential:
        An ``azure-identity`` ``TokenCredential``.  Defaults to
        ``DefaultAzureCredential()``.  Token acquisition and caching are
        handled entirely by the azure-identity library — no hand-rolled MSAL.
    scope:
        OAuth2 scope to request.  Must match the ``aud`` claim the API expects.
        Defaults to ``{api_app_id}/.default``.  Pass the full scope string:
        ``api://<api-app-id>/.default``.
    timeout_s:
        Default HTTP timeout in seconds for all requests (default ``30.0``).
    """

    def __init__(
        self,
        api_url: str,
        credential: TokenCredential | None = None,
        scope: str | None = None,
        timeout_s: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self._api_url = api_url.rstrip("/")
        self._credential: TokenCredential = credential or DefaultAzureCredential()
        self._scope = scope  # caller MUST pass api://<app-id>/.default
        self._timeout_s = timeout_s

        if self._scope is None:
            raise ValueError(
                "scope is required. Pass api://<api-app-id>/.default "
                "(the API application's App ID URI)."
            )

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #
    def _get_token(self) -> str:
        """Acquire a fresh access token (azure-identity caches internally)."""
        token = self._credential.get_token(self._scope)  # type: ignore[arg-type]
        return token.token

    def _sync_client(self) -> httpx.Client:
        return httpx.Client(timeout=self._timeout_s)

    def _async_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self._timeout_s)

    # ------------------------------------------------------------------ #
    # Sync API — public
    # ------------------------------------------------------------------ #
    def create_session(
        self,
        image: str,
        low_latency: bool = False,
        env: dict[str, str] | None = None,
    ) -> _SessionHandle:
        """Create a new sandbox session.

        Parameters
        ----------
        image:
            Fully-qualified container image reference, e.g.
            ``acr.example.azurecr.io/sandbox/base/python:3.12``.
        low_latency:
            If ``True``, request a pod from the shared warm-pool tier
            (``IdentityTier.SHARED_WARM_POOL``).  Requires the
            ``SandboxLowLatency`` Entra role assignment.
        env:
            Optional environment variables to inject into the sandbox.
        """
        payload: dict[str, Any] = {"image": image, "low_latency": low_latency}
        if env:
            payload["env"] = env

        headers = _auth_headers(self._get_token(), effective_traceparent())
        with self._sync_client() as http:
            resp = http.post(
                f"{self._api_url}/sessions", json=payload, headers=headers
            )
        _raise_for_status(resp)
        return _SessionHandle(self, Session.model_validate(resp.json()))

    def list_sessions(self) -> list[_SessionHandle]:
        """Return all active sessions visible to the caller."""
        headers = _auth_headers(self._get_token(), effective_traceparent())
        with self._sync_client() as http:
            resp = http.get(f"{self._api_url}/sessions", headers=headers)
        _raise_for_status(resp)
        return [_SessionHandle(self, Session.model_validate(s)) for s in resp.json()]

    def get_session(self, session_id: str) -> _SessionHandle:
        """Fetch a single session by ID."""
        headers = _auth_headers(self._get_token(), effective_traceparent())
        with self._sync_client() as http:
            resp = http.get(
                f"{self._api_url}/sessions/{session_id}", headers=headers
            )
        _raise_for_status(resp, session_id=session_id)
        return _SessionHandle(self, Session.model_validate(resp.json()))

    # ------------------------------------------------------------------ #
    # Sync API — internal (called by _SessionHandle)
    # ------------------------------------------------------------------ #
    def _run(
        self, session_id: str, command: str, timeout_s: float
    ) -> RunResult:
        payload = {"command": command}
        headers = _auth_headers(self._get_token(), effective_traceparent())
        with httpx.Client(timeout=timeout_s) as http:
            resp = http.post(
                f"{self._api_url}/sessions/{session_id}/run",
                json=payload,
                headers=headers,
            )
        _raise_for_status(resp, session_id=session_id)
        return RunResult.model_validate(resp.json())

    def _delete_session(self, session_id: str) -> None:
        headers = _auth_headers(self._get_token(), effective_traceparent())
        with self._sync_client() as http:
            resp = http.delete(
                f"{self._api_url}/sessions/{session_id}", headers=headers
            )
        _raise_for_status(resp, session_id=session_id)

    # ------------------------------------------------------------------ #
    # Async API — public
    # ------------------------------------------------------------------ #
    async def acreate_session(
        self,
        image: str,
        low_latency: bool = False,
        env: dict[str, str] | None = None,
    ) -> _SessionHandle:
        """Async counterpart of :meth:`create_session`."""
        payload: dict[str, Any] = {"image": image, "low_latency": low_latency}
        if env:
            payload["env"] = env

        headers = _auth_headers(self._get_token(), effective_traceparent())
        async with self._async_client() as http:
            resp = await http.post(
                f"{self._api_url}/sessions", json=payload, headers=headers
            )
        _raise_for_status(resp)
        return _SessionHandle(self, Session.model_validate(resp.json()))

    async def alist_sessions(self) -> list[_SessionHandle]:
        """Async counterpart of :meth:`list_sessions`."""
        headers = _auth_headers(self._get_token(), effective_traceparent())
        async with self._async_client() as http:
            resp = await http.get(f"{self._api_url}/sessions", headers=headers)
        _raise_for_status(resp)
        return [_SessionHandle(self, Session.model_validate(s)) for s in resp.json()]

    async def aget_session(self, session_id: str) -> _SessionHandle:
        """Async counterpart of :meth:`get_session`."""
        headers = _auth_headers(self._get_token(), effective_traceparent())
        async with self._async_client() as http:
            resp = await http.get(
                f"{self._api_url}/sessions/{session_id}", headers=headers
            )
        _raise_for_status(resp, session_id=session_id)
        return _SessionHandle(self, Session.model_validate(resp.json()))

    # ------------------------------------------------------------------ #
    # Async API — internal (called by _SessionHandle)
    # ------------------------------------------------------------------ #
    async def _arun(
        self, session_id: str, command: str, timeout_s: float
    ) -> RunResult:
        payload = {"command": command}
        headers = _auth_headers(self._get_token(), effective_traceparent())
        async with httpx.AsyncClient(timeout=timeout_s) as http:
            resp = await http.post(
                f"{self._api_url}/sessions/{session_id}/run",
                json=payload,
                headers=headers,
            )
        _raise_for_status(resp, session_id=session_id)
        return RunResult.model_validate(resp.json())

    async def _adelete_session(self, session_id: str) -> None:
        headers = _auth_headers(self._get_token(), effective_traceparent())
        async with self._async_client() as http:
            resp = await http.delete(
                f"{self._api_url}/sessions/{session_id}", headers=headers
            )
        _raise_for_status(resp, session_id=session_id)
