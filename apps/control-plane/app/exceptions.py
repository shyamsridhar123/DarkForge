"""Custom exception hierarchy for the control plane."""

from __future__ import annotations


class ControlPlaneError(Exception):
    """Base class for all control-plane errors."""

    http_status: int = 500
    error_code: str = "INTERNAL_ERROR"

    def __init__(self, message: str, detail: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail


# ── Auth ──────────────────────────────────────────────────────────────────────

class InvalidTokenError(ControlPlaneError):
    """JWT is structurally invalid, signature fails, or standard claims are wrong."""

    http_status = 401
    error_code = "INVALID_TOKEN"


class WrongAudienceError(InvalidTokenError):
    """Token audience does not match the OpenSandbox API app ID. Plan AC #4."""

    error_code = "WRONG_AUDIENCE"


class TokenExpiredError(InvalidTokenError):
    """Token exp claim is in the past."""

    error_code = "TOKEN_EXPIRED"


class InsufficientScopeError(ControlPlaneError):
    """Caller's token does not carry the required scope/role."""

    http_status = 403
    error_code = "INSUFFICIENT_SCOPE"


class OBOExchangeError(ControlPlaneError):
    """MSAL OBO exchange failed �� AADSTS error or network error."""

    http_status = 502
    error_code = "OBO_EXCHANGE_FAILED"


# ── Session / sandbox ─────────────────────────────────────────────────────────

class SessionNotFoundError(ControlPlaneError):
    http_status = 404
    error_code = "SESSION_NOT_FOUND"


class SessionOwnershipError(ControlPlaneError):
    """Caller is not the owner of the requested session."""

    http_status = 403
    error_code = "FORBIDDEN_NOT_OWNER"


class ImageNotAllowedError(ControlPlaneError):
    """Requested image is not in the curated catalog."""

    http_status = 422
    error_code = "IMAGE_NOT_ALLOWED"


class RateLimitError(ControlPlaneError):
    """Per-user or platform-wide rate limit exceeded."""

    http_status = 429
    error_code = "RATE_LIMIT_EXCEEDED"


# ── Provisioning ──────────────────────────────────────────────────────────────

class PropagationTimeoutError(ControlPlaneError):
    """Workload Identity federated-credential propagation probe timed out. Plan S1 / Failure #3."""

    http_status = 503
    error_code = "PROPAGATION_TIMEOUT"


class UserAlreadyProvisionedError(ControlPlaneError):
    http_status = 409
    error_code = "USER_ALREADY_PROVISIONED"


# ── AKS client ────────────────────────────────────────────────────────────────

class AKSClientError(ControlPlaneError):
    http_status = 502
    error_code = "AKS_ERROR"
