"""
Exceptions for the OpenSandbox Azure SDK.

HTTP status → exception mapping:
  401 → AuthenticationError
  403 → AuthorizationError
  404 → SessionNotFoundError
  429 → RateLimitError
  503 → PropagationTimeoutError  (workload-identity FC propagation race)
"""

from __future__ import annotations


class SandboxError(Exception):
    """Base class for all OpenSandbox SDK errors."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class AuthenticationError(SandboxError):
    """Raised when the server returns 401 (missing or invalid bearer token / OBO exchange failure)."""


class AuthorizationError(SandboxError):
    """Raised when the server returns 403 (valid identity, insufficient permissions)."""


class RateLimitError(SandboxError):
    """Raised when the server returns 429.

    Attributes:
        retry_after: Value of the Retry-After header in seconds, or None if absent.
    """

    def __init__(self, message: str, retry_after: int | None = None) -> None:
        super().__init__(message, status_code=429)
        self.retry_after = retry_after


class SessionNotFoundError(SandboxError):
    """Raised when the server returns 404 for a session lookup."""

    def __init__(self, session_id: str) -> None:
        super().__init__(f"Session not found: {session_id}", status_code=404)
        self.session_id = session_id


class PropagationTimeoutError(SandboxError):
    """Raised when the server returns 503 during workload-identity FC propagation.

    The server signals that the user's identity is still being federated to the
    pod (Workload Identity propagation race — see AC #4 / mitigation Task 3.2).
    The client should back off and retry using the ``retry_after`` hint.

    Attributes:
        retry_after: Value of the Retry-After header in seconds, or None if absent.
    """

    def __init__(self, message: str, retry_after: int | None = None) -> None:
        super().__init__(message, status_code=503)
        self.retry_after = retry_after
