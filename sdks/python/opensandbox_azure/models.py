"""
Pydantic models for the OpenSandbox Azure SDK.
"""

from __future__ import annotations

import enum
from datetime import datetime

from pydantic import BaseModel, Field


class SessionState(str, enum.Enum):
    """Lifecycle state of a sandbox session."""

    PENDING = "pending"
    """Pod is being scheduled / Kata VM is initialising."""

    RUNNING = "running"
    """Execution daemon is ready to accept commands."""

    TERMINATING = "terminating"
    """Graceful shutdown in progress."""

    TERMINATED = "terminated"
    """Session has ended; resources reclaimed."""

    ERROR = "error"
    """Unrecoverable error; session cannot be used."""


class IdentityTier(str, enum.Enum):
    """Identity isolation tier for the session.

    USER_BOUND:
        The pod runs with a per-user UAMI (Workload Identity FC).
        Every action traces to the calling user's Entra OID.
        Required for the default security posture.

    SHARED_WARM_POOL:
        The pod is taken from a pre-warmed shared pool. Provides lower cold-start
        latency at the cost of shared identity (UAMI owned by the control plane).
        Requires the ``SandboxLowLatency`` Entra role assignment.
        Opt-in via ``low_latency=True`` on ``create_session``.
        All shared-pool commands are rate-limited and audit-logged with the
        calling user's OID attached as a claim.
    """

    USER_BOUND = "user_bound"
    SHARED_WARM_POOL = "shared_warm_pool"


class Session(BaseModel):
    """Representation of a running sandbox session returned by the server."""

    session_id: str = Field(..., description="Stable unique identifier for the session.")
    image: str = Field(..., description="Container image used for this session.")
    state: SessionState
    identity_tier: IdentityTier = IdentityTier.USER_BOUND
    created_at: datetime
    node_name: str | None = Field(
        None, description="AKS node the pod was scheduled on (informational)."
    )
    low_latency: bool = False


class RunResult(BaseModel):
    """Result of a command executed inside a sandbox session."""

    session_id: str
    command: str
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    duration_ms: int = Field(..., description="Wall-clock execution time in milliseconds.")
    trace_id: str | None = Field(
        None, description="W3C trace ID from the traceparent header echoed by the server."
    )
