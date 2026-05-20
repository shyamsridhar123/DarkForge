"""
opensandbox-azure — Public API surface.

    from opensandbox_azure import SandboxClient, Session, RunResult
    from opensandbox_azure.exceptions import SandboxError, AuthenticationError, ...
"""

from .client import SandboxClient
from .exceptions import (
    AuthenticationError,
    AuthorizationError,
    PropagationTimeoutError,
    RateLimitError,
    SandboxError,
    SessionNotFoundError,
)
from .models import IdentityTier, RunResult, Session, SessionState

__all__ = [
    "SandboxClient",
    # Models
    "Session",
    "RunResult",
    "SessionState",
    "IdentityTier",
    # Exceptions
    "SandboxError",
    "AuthenticationError",
    "AuthorizationError",
    "RateLimitError",
    "SessionNotFoundError",
    "PropagationTimeoutError",
]
