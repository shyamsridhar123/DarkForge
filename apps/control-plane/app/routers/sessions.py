"""Sessions router — create, query, run commands, stream logs, delete sandboxes."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.aks_client import (
    create_sandbox_pod,
    delete_sandbox_pod,
    exec_in_sandbox,
    get_sandbox_pod,
)
from app.auth.dependencies import CurrentUser, UserClaims
from app.config import get_settings
from app.exceptions import (
    ImageNotAllowedError,
    InsufficientScopeError,
    SessionNotFoundError,
    SessionOwnershipError,
)
from app.middleware.audit import emit_command_audit_event

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sessions", tags=["sessions"])

# In-memory session store (replace with Redis/CosmosDB in production)
_sessions: dict[str, dict] = {}

LOW_LATENCY_ROLE = "Sandbox.LowLatency"


class CreateSessionRequest(BaseModel):
    image: str = Field(..., description="Container image tag (short form, without registry prefix)")
    low_latency: bool = Field(False, description="Use shared warm-pool tier (requires role)")
    env: dict[str, str] = Field(default_factory=dict, description="Environment variables")


class RunCommandRequest(BaseModel):
    command: str = Field(..., description="Shell command to execute in the sandbox")
    timeout_s: int = Field(60, ge=1, le=3600, description="Command timeout in seconds")


def _validate_image(image: str) -> str:
    """Return the fully-qualified image ref if allowed, else raise ImageNotAllowedError."""
    settings = get_settings()
    acr = settings.acr_fqdn

    # Accept either short form (e.g. "python:3.12") or fully qualified
    if image.startswith(acr + "/"):
        fq_image = image
        short = image[len(acr) + 1:]
    else:
        short = image
        fq_image = f"{acr}/{image}"

    # If a curated list is configured, enforce it
    if settings.curated_images and short not in settings.curated_images:
        raise ImageNotAllowedError(
            f"Image {short!r} is not in the curated catalog",
            detail=f"Allowed images: {settings.curated_images}",
        )
    return fq_image


@router.post("", status_code=201)
async def create_session(
    body: CreateSessionRequest,
    user: CurrentUser,
    request: Request,
) -> JSONResponse:
    """
    POST /sessions — create a new sandbox session.

    low_latency=True requires the Sandbox.LowLatency role (plan Task 3.4).
    """
    # Store user OID on request state for audit middleware
    request.state.user_oid = user.oid

    # Role check for low-latency tier
    if body.low_latency and not user.has_role(LOW_LATENCY_ROLE):
        raise InsufficientScopeError(
            f"Role {LOW_LATENCY_ROLE!r} required to use low-latency tier"
        )

    fq_image = _validate_image(body.image)

    result = await create_sandbox_pod(
        user_claims=user,
        image=fq_image,
        low_latency=body.low_latency,
        env=body.env,
    )

    # Persist session metadata
    session_id = result["session_id"]
    _sessions[session_id] = {
        **result,
        "owner_oid": user.oid,
        "image": fq_image,
        "status": "running",
    }

    logger.info(
        "Session created session_id=%s user_oid=%s identity_tier=%s",
        session_id, user.oid, result["identity_tier"],
    )
    return JSONResponse(
        {
            "session_id": session_id,
            "connection_info": result["connection_info"],
            "identity_tier": result["identity_tier"],
        },
        status_code=201,
    )


@router.get("/{session_id}")
async def get_session(
    session_id: Annotated[str, Path()],
    user: CurrentUser,
    request: Request,
) -> JSONResponse:
    """GET /sessions/{session_id} — returns session metadata. 403 if not owner."""
    request.state.user_oid = user.oid

    session = _sessions.get(session_id)
    if not session:
        raise SessionNotFoundError(f"Session {session_id!r} not found")
    if session["owner_oid"] != user.oid:
        raise SessionOwnershipError("You are not the owner of this session")

    # Enrich with live pod status
    pod_info = await get_sandbox_pod(
        namespace=session["namespace"],
        pod_name=session["pod_name"],
        user_claims=user,
    )
    return JSONResponse({**session, "pod_status": pod_info.get("phase", "unknown")})


@router.post("/{session_id}/run")
async def run_command(
    session_id: Annotated[str, Path()],
    body: RunCommandRequest,
    user: CurrentUser,
    request: Request,
) -> JSONResponse:
    """POST /sessions/{session_id}/run — execute a command inside the sandbox."""
    request.state.user_oid = user.oid

    session = _sessions.get(session_id)
    if not session:
        raise SessionNotFoundError(f"Session {session_id!r} not found")
    if session["owner_oid"] != user.oid:
        raise SessionOwnershipError("You are not the owner of this session")

    result = await exec_in_sandbox(
        session_id=session_id,
        namespace=session["namespace"],
        pod_name=session["pod_name"],
        command=body.command,
        timeout_s=body.timeout_s,
        user_claims=user,
    )

    # Emit audit event to Event Hubs (command hash only — not the command itself)
    trace_id = getattr(request.state, "trace_id", "")
    await emit_command_audit_event(
        user_oid=user.oid,
        session_id=session_id,
        command=body.command,
        exit_code=result.get("exit_code", -1),
        identity_tier=session.get("identity_tier", "per-user"),
        trace_id=trace_id,
    )

    return JSONResponse(result)


@router.get("/{session_id}/logs")
async def get_logs(
    session_id: Annotated[str, Path()],
    user: CurrentUser,
    request: Request,
) -> JSONResponse:
    """GET /sessions/{session_id}/logs — proxy execd logs (stub)."""
    request.state.user_oid = user.oid

    session = _sessions.get(session_id)
    if not session:
        raise SessionNotFoundError(f"Session {session_id!r} not found")
    if session["owner_oid"] != user.oid:
        raise SessionOwnershipError("You are not the owner of this session")

    # STUB: In production, proxy GET http://<pod-ip>:8888/logs from execd
    logger.info("Log proxy requested session_id=%s user_oid=%s", session_id, user.oid)
    return JSONResponse({"session_id": session_id, "logs": []})


@router.delete("/{session_id}", status_code=204)
async def delete_session(
    session_id: Annotated[str, Path()],
    user: CurrentUser,
    request: Request,
) -> None:
    """DELETE /sessions/{session_id} — terminate the sandbox pod."""
    request.state.user_oid = user.oid

    session = _sessions.get(session_id)
    if not session:
        raise SessionNotFoundError(f"Session {session_id!r} not found")
    if session["owner_oid"] != user.oid:
        raise SessionOwnershipError("You are not the owner of this session")

    await delete_sandbox_pod(
        namespace=session["namespace"],
        pod_name=session["pod_name"],
        user_claims=user,
        identity_tier=session.get("identity_tier", "per-user"),
    )
    _sessions.pop(session_id, None)
    logger.info("Session deleted session_id=%s user_oid=%s", session_id, user.oid)
