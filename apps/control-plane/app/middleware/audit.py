"""Audit middleware: structured logging + Event Hubs emission for session commands."""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)

# Lazy import to avoid startup cost when EventHub not configured
_eventhub_producer = None
_eventhub_init_attempted = False


def _get_eventhub_producer():
    global _eventhub_producer, _eventhub_init_attempted
    if _eventhub_init_attempted:
        return _eventhub_producer
    _eventhub_init_attempted = True
    try:
        from app.config import get_settings
        settings = get_settings()
        if not settings.eventhub_connection_string:
            return None
        from azure.eventhub import EventHubProducerClient
        _eventhub_producer = EventHubProducerClient.from_connection_string(
            conn_str=settings.eventhub_connection_string,
            eventhub_name=settings.eventhub_name,
        )
        logger.info("EventHub producer initialized for audit events")
    except Exception as exc:
        logger.warning("EventHub producer init failed (audit events disabled): %s", exc)
    return _eventhub_producer


def _parse_traceparent(header: str | None) -> tuple[str, str]:
    """Extract trace_id and span_id from W3C traceparent header, or generate new ones."""
    if header:
        parts = header.split("-")
        if len(parts) == 4:
            return parts[1], parts[2]
    trace_id = uuid.uuid4().hex
    span_id = uuid.uuid4().hex[:16]
    return trace_id, span_id


class AuditMiddleware(BaseHTTPMiddleware):
    """
    Per-request structured audit logging.

    For POST /sessions/{id}/run, also emits an audit event to Event Hubs
    with schema: { user_oid, session_id, command_hash, exit_code, ts, identity_tier }.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        start = time.monotonic()
        trace_id, span_id = _parse_traceparent(request.headers.get("traceparent"))

        # Store trace context on request state so downstream can use it
        request.state.trace_id = trace_id
        request.state.span_id = span_id

        # Extract session_id from path if present
        session_id = request.path_params.get("session_id", "")

        # Extract user OID (best-effort; may be absent for unauthenticated paths)
        user_oid = ""

        response = await call_next(request)

        duration_ms = int((time.monotonic() - start) * 1000)

        # Try to get user_oid from request state (set by auth dependency)
        user_oid = getattr(request.state, "user_oid", "")

        log_record = {
            "event": "http_request",
            "op": f"{request.method} {request.url.path}",
            "trace_id": trace_id,
            "span_id": span_id,
            "user_oid": user_oid,
            "session_id": session_id,
            "http_status": response.status_code,
            "duration_ms": duration_ms,
            "method": request.method,
            "path": request.url.path,
        }
        logger.info("audit", extra=log_record)

        return response


async def emit_command_audit_event(
    user_oid: str,
    session_id: str,
    command: str,
    exit_code: int,
    identity_tier: str,
    trace_id: str,
) -> None:
    """
    Emit an audit event to Event Hubs for a session command execution.

    Schema: { user_oid, session_id, command_hash, exit_code, ts, identity_tier }
    The command itself is NOT included — only a SHA-256 hash to avoid bloating
    the fast-path audit stream (per plan Task 1.6 / B-C3).
    """
    import json
    import time as _time

    command_hash = hashlib.sha256(command.encode()).hexdigest()
    event_body = {
        "user_oid": user_oid,
        "session_id": session_id,
        "command_hash": command_hash,
        "exit_code": exit_code,
        "ts": _time.time(),
        "identity_tier": identity_tier,
        "trace_id": trace_id,
    }

    producer = _get_eventhub_producer()
    if producer is None:
        logger.debug("EventHub not configured — skipping audit event for session %s", session_id)
        return

    try:
        from azure.eventhub import EventData
        batch = await _async_create_batch(producer)
        batch.add(EventData(json.dumps(event_body)))
        await _async_send_batch(producer, batch)
        logger.debug("Audit event emitted for session_id=%s", session_id)
    except Exception as exc:
        # Audit failures must NOT block the response — log and continue
        logger.error("Failed to emit audit event session_id=%s: %s", session_id, exc)


async def _async_create_batch(producer):
    """Wrap sync EventHub create_batch in thread executor."""
    import asyncio
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, producer.create_batch)


async def _async_send_batch(producer, batch):
    """Wrap sync EventHub send_batch in thread executor."""
    import asyncio
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, producer.send_batch, batch)
