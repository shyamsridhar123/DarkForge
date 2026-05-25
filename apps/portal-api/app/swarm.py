from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import HTTPException

from .config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

_MAX_EVENTS = 500
_MAX_RUNS = 10


class RunHandle:
    def __init__(
        self,
        run_id: str,
        n: int,
        model: str,
        image: str,
        aad_token: str,
    ) -> None:
        self.run_id = run_id
        self.n = n
        self.model = model
        self.image = image
        self.started_at: datetime = datetime.now(timezone.utc)
        self.finished_at: datetime | None = None
        self.state: str = "running"
        self.proc: subprocess.Popen | None = None  # type: ignore[type-arg]
        self.events: list[dict] = []
        self.queue: asyncio.Queue[dict | None] = asyncio.Queue()
        self.leaderboard: list[dict] = []
        self.summary: dict | None = None
        self.aad_token: str = aad_token  # never logged


# Module-level registry — keyed by run_id hex string
_registry: dict[str, RunHandle] = {}

# ---------------------------------------------------------------------------
# az CLI path resolution (handles Windows where az.cmd may not be on PATH
# inside the uvicorn subprocess environment)
# ---------------------------------------------------------------------------

_AZ_FALLBACK_PATHS = [
    r"C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin\az.cmd",
    r"C:\Program Files (x86)\Microsoft SDKs\Azure\CLI2\wbin\az.cmd",
]


def _find_az() -> str:
    """Return the path to the az CLI executable, or raise HTTPException."""
    found = shutil.which("az") or shutil.which("az.cmd")
    if found:
        return found
    for candidate in _AZ_FALLBACK_PATHS:
        if os.path.isfile(candidate):
            return candidate
    raise HTTPException(
        status_code=503,
        detail="az CLI not found. Install Azure CLI and run az login.",
    )

# ---------------------------------------------------------------------------
# Regex patterns (from plan Card 2)
# ---------------------------------------------------------------------------

_RE_PHASE_KIMI = re.compile(
    r"^\[\+\] Kimi deployment=(\S+), response_chars=(\d+), took ([\d.]+)s"
)
_RE_RESULT = re.compile(
    r"^\s+\[#(\d+)\]\s+(PASS|FAIL)\s+in\s+([\d.]+)s\s+[—\-]+\s+(.+)$"
)
_RE_SPEEDUP = re.compile(r"^\s+Speedup vs\.\s+serial:\s+([\d.]+)x")
_RE_WALL = re.compile(r"^\s+Swarm wall-clock:\s+([\d.]+)s")
_RE_PASSED = re.compile(r"^\s+Passed:\s+(\d+)")
_RE_HYPOTHESES = re.compile(r"^\s+Hypotheses:\s+(\d+)")
_RE_WINNER = re.compile(r"^WINNER:\s+hypothesis #(\d+)")
_RE_SEPARATOR = re.compile(r"^={3,}$")

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _push_event(handle: RunHandle, evt: dict) -> None:
    """Append event to bounded list and enqueue for live consumers."""
    if len(handle.events) >= _MAX_EVENTS:
        handle.events.pop(0)
    handle.events.append(evt)
    handle.queue.put_nowait(evt)


def _parse_line(handle: RunHandle, line: str) -> None:
    """Parse one stdout line; emit structured events as appropriate."""
    stripped = line.rstrip()

    # Skip blank lines and separator bars
    if not stripped or _RE_SEPARATOR.match(stripped):
        return

    m = _RE_PHASE_KIMI.match(stripped)
    if m:
        _push_event(
            handle,
            {
                "type": "phase",
                "data": {
                    "phase": "hypotheses_generated",
                    "deployment": m.group(1),
                    "chars": int(m.group(2)),
                    "took_s": float(m.group(3)),
                },
            },
        )
        return

    m = _RE_RESULT.match(stripped)
    if m:
        entry = {
            "idx": int(m.group(1)),
            "status": m.group(2),
            "duration_s": float(m.group(3)),
            "diagnosis": m.group(4).strip(),
        }
        handle.leaderboard.append(entry)
        _push_event(handle, {"type": "result", "data": entry})
        return

    m = _RE_SPEEDUP.match(stripped)
    if m:
        if handle.summary is None:
            handle.summary = {}
        handle.summary["speedup"] = float(m.group(1))
        _push_event(
            handle,
            {"type": "summary", "data": {"speedup": float(m.group(1))}},
        )
        return

    m = _RE_WALL.match(stripped)
    if m:
        if handle.summary is None:
            handle.summary = {}
        handle.summary["wall_s"] = float(m.group(1))
        return

    m = _RE_PASSED.match(stripped)
    if m:
        if handle.summary is None:
            handle.summary = {}
        handle.summary["passes"] = int(m.group(1))
        return

    m = _RE_HYPOTHESES.match(stripped)
    if m:
        if handle.summary is None:
            handle.summary = {}
        handle.summary["total"] = int(m.group(1))
        return

    m = _RE_WINNER.match(stripped)
    if m:
        _push_event(
            handle,
            {"type": "phase", "data": {"phase": "winner", "idx": int(m.group(1))}},
        )
        return

    # Fallback: raw log line
    _push_event(handle, {"type": "log", "data": {"line": stripped}})


def _drain_stdout(handle: RunHandle) -> None:
    """Blocking I/O: read proc stdout line-by-line, parse, push events."""
    proc = handle.proc
    if proc is None or proc.stdout is None:
        return
    try:
        for line in proc.stdout:
            _parse_line(handle, line)
        proc.wait()
    except Exception as exc:
        logger.warning("swarm drain error run=%s: %s", handle.run_id, exc)
    finally:
        exit_code = proc.returncode if proc.returncode is not None else -1
        handle.state = "completed" if exit_code == 0 else "failed"
        handle.finished_at = datetime.now(timezone.utc)
        done_evt = {
            "type": "done",
            "data": {"exit_code": exit_code, "state": handle.state},
        }
        _push_event(handle, done_evt)
        handle.queue.put_nowait(None)  # sentinel for stream_events
        logger.info(
            "swarm run=%s finished state=%s exit_code=%s",
            handle.run_id,
            handle.state,
            exit_code,
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def start_run(
    n: int,
    model: str,
    image: str | None = None,
) -> str:
    """Mint AAD token, spawn subprocess, register handle, return run_id."""
    # Mint fresh AAD token via az CLI
    az_cmd = _find_az()
    try:
        result = await asyncio.to_thread(
            subprocess.check_output,
            [
                az_cmd,
                "account",
                "get-access-token",
                "--resource",
                "https://cognitiveservices.azure.com",
                "--query",
                "accessToken",
                "-o",
                "tsv",
            ],
            text=True,
            stderr=subprocess.STDOUT,
        )
        token = result.strip()
    except subprocess.CalledProcessError as exc:
        logger.error("az token mint failed: %s", exc.output)
        raise HTTPException(
            status_code=503,
            detail="Failed to mint AAD token via az CLI. Is az login active?",
        ) from exc

    resolved_image = image or settings.SWARM_DEFAULT_IMAGE
    run_id = uuid4().hex

    handle = RunHandle(
        run_id=run_id,
        n=n,
        model=model,
        image=resolved_image,
        aad_token=token,  # stored for potential refresh; never logged
    )

    env = os.environ.copy()
    env.update(
        {
            "N_HYPOTHESES": str(n),
            "SANDBOX_IMAGE": resolved_image,
            "AAD_TOKEN": token,
            "PYTHONUNBUFFERED": "1",
        }
    )

    proc = subprocess.Popen(  # noqa: S603
        [str(settings.SWARM_VENV_PYTHON), str(settings.SWARM_SCRIPT)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        bufsize=1,
        text=True,
        cwd=str(settings.REPO_ROOT),
    )
    handle.proc = proc

    # Evict oldest run if registry is full
    if len(_registry) >= _MAX_RUNS:
        oldest_id = min(
            _registry,
            key=lambda rid: _registry[rid].started_at,
        )
        _registry.pop(oldest_id, None)

    _registry[run_id] = handle

    # Spin up background reader thread
    asyncio.create_task(_run_reader(handle))

    logger.info(
        "swarm run=%s started n=%s model=%s image=%s pid=%s",
        run_id,
        n,
        model,
        resolved_image,
        proc.pid,
    )
    return run_id


async def _run_reader(handle: RunHandle) -> None:
    """Async task: offload blocking stdout drain to a thread."""
    try:
        await asyncio.to_thread(_drain_stdout, handle)
    except Exception as exc:
        logger.exception("reader task error run=%s: %s", handle.run_id, exc)


async def stream_events(run_id: str) -> AsyncIterator[dict]:
    """
    Async generator for SSE.  Replays cached events first (for late subscribers /
    page reload), then follows the live queue until the sentinel arrives.
    """
    handle = _registry.get(run_id)
    if handle is None:
        # Yield a single error event and close
        yield {"type": "error", "data": {"detail": f"run {run_id!r} not found"}}
        return

    # Replay historical events
    for evt in list(handle.events):
        yield evt
        # If the run is already done and we just yielded the done event, stop.
        if evt.get("type") == "done":
            return

    # Follow live queue until process finishes
    while handle.state == "running":
        try:
            evt = await asyncio.wait_for(handle.queue.get(), timeout=15.0)
        except asyncio.TimeoutError:
            yield {"type": "heartbeat", "data": {}}
            continue

        if evt is None:
            # Sentinel: process exited
            break
        yield evt
        if evt.get("type") == "done":
            break


def get_run(run_id: str) -> RunHandle | None:
    return _registry.get(run_id)


def list_runs() -> list[dict]:
    """Return last 10 runs, most-recent-first, summary fields only."""
    runs = sorted(_registry.values(), key=lambda h: h.started_at, reverse=True)
    result = []
    for h in runs[:_MAX_RUNS]:
        summary = h.summary or {}
        result.append(
            {
                "run_id": h.run_id,
                "state": h.state,
                "n": h.n,
                "model": h.model,
                "image": h.image,
                "started_at": h.started_at.isoformat(),
                "finished_at": h.finished_at.isoformat() if h.finished_at else None,
                "passes": summary.get("passes"),
                "total": summary.get("total"),
                "speedup": summary.get("speedup"),
            }
        )
    return result


async def cancel_run(run_id: str) -> bool:
    handle = _registry.get(run_id)
    if handle is None:
        return False
    if handle.state != "running":
        return False

    proc = handle.proc
    if proc is not None and proc.poll() is None:
        proc.terminate()
        try:
            await asyncio.wait_for(asyncio.to_thread(proc.wait), timeout=2.0)
        except asyncio.TimeoutError:
            proc.kill()

    handle.state = "cancelled"
    handle.finished_at = datetime.now(timezone.utc)
    done_evt = {"type": "done", "data": {"exit_code": -1, "state": "cancelled"}}
    _push_event(handle, done_evt)
    handle.queue.put_nowait(None)
    logger.info("swarm run=%s cancelled", run_id)
    return True
