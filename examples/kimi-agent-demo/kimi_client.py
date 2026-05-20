"""
kimi_client.py — Thin Kimi K2.5 client for Azure AI Foundry.

Uses DefaultAzureCredential (Managed Identity / az CLI) to acquire bearer tokens.
Tokens are cached internally by the credential object — we call get_token() on
every request so the cache is consulted automatically (no raw token storage).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from azure.core.credentials import TokenCredential
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

_AUDIENCE = "https://cognitiveservices.azure.com/.default"


def _is_retryable(exc: BaseException) -> bool:
    """Retry on 429 / 5xx HTTP status codes."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in {429, 500, 502, 503, 504}
    return False


class KimiClient:
    """Minimal synchronous HTTP client for the Kimi K2.5 chat-completions endpoint."""

    def __init__(
        self,
        endpoint: str,
        credential: TokenCredential,
        model: str = "Kimi-K2.5",
        timeout: float = 120.0,
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._credential = credential
        self._model = model
        self._http = httpx.Client(timeout=timeout)

    def _bearer(self) -> str:
        token = self._credential.get_token(_AUDIENCE)
        return f"Bearer {token.token}"

    @retry(
        retry=retry_if_exception(_is_retryable),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 2048,
        temperature: float = 0.0,
    ) -> dict[str, Any]:
        """
        POST a chat-completions request to the Kimi endpoint.

        Returns the raw response dict (choices, usage, etc.).
        Raises httpx.HTTPStatusError on non-retryable errors.
        """
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        headers = {
            "Authorization": self._bearer(),
            "Content-Type": "application/json",
        }

        logger.debug("POST %s  model=%s  messages=%d", self._endpoint, self._model, len(messages))
        resp = self._http.post(self._endpoint, json=payload, headers=headers)

        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning("HTTP %s from Kimi endpoint: %s", exc.response.status_code, exc.response.text[:300])
            raise

        return resp.json()

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "KimiClient":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()
