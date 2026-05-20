"""
W3C traceparent header generation.

Spec: https://www.w3.org/TR/trace-context/
Format: 00-<trace-id>-<parent-id>-<flags>

Each SDK call generates a *new* traceparent so the ACA control plane, AKS API
server, Cilium Hubble flow logs, and execution-daemon stdout can be correlated
end-to-end (Critic S-C9).

The version byte is fixed at ``00``; the flags byte is ``01`` (sampled).
"""

from __future__ import annotations

import os
import secrets


def generate_traceparent() -> str:
    """Return a fresh W3C traceparent header value for a single outbound request.

    Example::

        traceparent: 00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01
    """
    trace_id = secrets.token_hex(16)   # 128-bit / 32 hex chars
    parent_id = secrets.token_hex(8)   # 64-bit  / 16 hex chars
    return f"00-{trace_id}-{parent_id}-01"


def traceparent_from_env() -> str | None:
    """Return the traceparent from ``TRACEPARENT`` env var if set, else None.

    Callers can propagate an existing trace context by setting the env var,
    which is the standard W3C propagation convention.
    """
    return os.environ.get("TRACEPARENT")


def effective_traceparent() -> str:
    """Return the env-provided traceparent or generate a fresh one."""
    return traceparent_from_env() or generate_traceparent()
