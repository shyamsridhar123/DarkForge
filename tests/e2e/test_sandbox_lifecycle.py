"""
tests/e2e/test_sandbox_lifecycle.py

Plan task: Phase 5 + AC #6 — Happy-path sandbox lifecycle with latency measurement.

Validates:
  1. POST /sessions → status Running (or equivalent).
  2. Execute a command in the session.
  3. DELETE /sessions/<id> completes cleanly.
  4. Cold-path p95 < 5s over 100 samples (default tier, warm nodes).
  5. Shared-tier p95 < 500ms over 100 samples (opt-in, steady-state pool).

Measurement protocol (AC #6):
  - Nodes must have been Ready for >= 120s with pre-warm DaemonSet livenessProbe passing.
  - Sample = wall-clock from POST /sessions to first byte of session response.
  - 100 samples per tier, sampled across 5 minutes.

Markers:
  @pytest.mark.requires_entra
  @pytest.mark.e2e
  @pytest.mark.slow
"""

from __future__ import annotations

import asyncio
import os
import statistics
import time
import uuid
from typing import Optional

import pytest

pytestmark = [
    pytest.mark.requires_entra,
    pytest.mark.e2e,
    pytest.mark.slow,
]

REQUIRED_ENV = [
    "CONTROL_PLANE_URL",
    "ENTRA_TEST_CLIENT_ID",
    "ENTRA_TEST_CLIENT_SECRET",
    "ENTRA_TENANT_ID",
    "ENTRA_SANDBOX_API_APP_ID",
]

COLD_PATH_P95_THRESHOLD = 5.0     # seconds (AC #6 default tier)
SHARED_PATH_P95_THRESHOLD = 0.5   # seconds (AC #6 shared tier)
COLD_SAMPLE_COUNT = 100
SHARED_SAMPLE_COUNT = 100
LATENCY_SAMPLE_WINDOW = 300       # 5 minutes
NODE_READY_MIN_AGE = 120          # seconds — per AC #6 measurement protocol


def _check_env():
    return [k for k in REQUIRED_ENV if not os.environ.get(k)]


@pytest.fixture(scope="module")
def config():
    missing = _check_env()
    if missing:
        pytest.skip(f"Missing env vars: {', '.join(missing)}")
    return {k: os.environ[k] for k in REQUIRED_ENV}


def _get_token(config: dict) -> str:
    try:
        import msal
    except ImportError:
        pytest.skip("msal not installed")
    app = msal.ConfidentialClientApplication(
        client_id=config["ENTRA_TEST_CLIENT_ID"],
        client_credential=config["ENTRA_TEST_CLIENT_SECRET"],
        authority=f"https://login.microsoftonline.com/{config['ENTRA_TENANT_ID']}",
    )
    result = app.acquire_token_for_client(
        scopes=[f"api://{config['ENTRA_SANDBOX_API_APP_ID']}/.default"]
    )
    assert "access_token" in result
    return result["access_token"]


async def _create_session(
    base_url: str,
    token: str,
    low_latency: bool = False,
    image: str = "python312-sandbox",
) -> tuple[float, dict]:
    """Create a session; return (latency_to_first_byte, response_body)."""
    import httpx
    async with httpx.AsyncClient(timeout=60) as client:
        start = time.monotonic()
        resp = await client.post(
            f"{base_url}/sessions",
            json={
                "image": image,
                "low_latency": low_latency,
                "test_session_id": f"lifecycle-{uuid.uuid4().hex[:12]}",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        latency = time.monotonic() - start
        resp.raise_for_status()
        return latency, resp.json()


async def _execute_in_session(
    base_url: str, token: str, session_id: str, code: str
) -> dict:
    import httpx
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{base_url}/sessions/{session_id}/execute",
            json={"code": code, "language": "python"},
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        return resp.json()


async def _delete_session(base_url: str, token: str, session_id: str) -> int:
    import httpx
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.delete(
            f"{base_url}/sessions/{session_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        return resp.status_code


# ---------------------------------------------------------------------------
# Test 1: Happy-path lifecycle (create → execute → delete)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_sandbox_happy_path_lifecycle(config):
    """Full lifecycle: create session → execute code → delete session."""
    token = _get_token(config)
    base_url = config["CONTROL_PLANE_URL"]

    latency, session = await _create_session(base_url, token)
    session_id = session.get("session_id") or session.get("id")
    assert session_id, f"No session_id in response: {session}"
    assert session.get("status") in ("running", "ready", "created"), (
        f"Unexpected session status: {session.get('status')}"
    )

    # Execute a trivial Python expression
    result = await _execute_in_session(
        base_url, token, session_id, "print('hello-sandbox')"
    )
    output = result.get("output") or result.get("stdout") or ""
    assert "hello-sandbox" in output, (
        f"Execution did not produce expected output. Result: {result}"
    )

    # Delete session
    delete_status = await _delete_session(base_url, token, session_id)
    assert delete_status in (200, 202, 204), (
        f"Session delete returned unexpected status {delete_status}"
    )


# ---------------------------------------------------------------------------
# Test 2: Cold-path p95 < 5s (AC #6 — default tier)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_cold_path_p95_latency(config):
    """
    AC #6 measurement: cold-path p95 < 5s over 100 samples.

    Nodes must have been Ready >= 120s before sampling.
    Samples are spread across 5 minutes.
    """
    token = _get_token(config)
    base_url = config["CONTROL_PLANE_URL"]

    latencies = []
    session_ids = []
    start_window = time.monotonic()

    for i in range(COLD_SAMPLE_COUNT):
        # Spread samples across the 5-minute window
        target_time = start_window + (i / COLD_SAMPLE_COUNT) * LATENCY_SAMPLE_WINDOW
        now = time.monotonic()
        if now < target_time:
            await asyncio.sleep(target_time - now)

        try:
            latency, session = await _create_session(base_url, token, low_latency=False)
            latencies.append(latency)
            sid = session.get("session_id") or session.get("id")
            if sid:
                session_ids.append(sid)
        except Exception as e:
            pytest.fail(f"Sample {i+1}/{COLD_SAMPLE_COUNT} failed: {e}")

    # Cleanup sessions
    cleanup_tasks = [_delete_session(base_url, token, sid) for sid in session_ids]
    await asyncio.gather(*cleanup_tasks, return_exceptions=True)

    latencies.sort()
    p95_idx = int(len(latencies) * 0.95)
    p95 = latencies[min(p95_idx, len(latencies) - 1)]

    assert p95 < COLD_PATH_P95_THRESHOLD, (
        f"Cold-path p95 latency {p95:.3f}s exceeds AC #6 threshold of {COLD_PATH_P95_THRESHOLD}s.\n"
        f"min={latencies[0]:.3f}s, median={statistics.median(latencies):.3f}s, "
        f"p95={p95:.3f}s, max={latencies[-1]:.3f}s\n"
        f"Ensure pre-warm DaemonSet livenessProbe is passing and nodes have been Ready >= {NODE_READY_MIN_AGE}s."
    )


# ---------------------------------------------------------------------------
# Test 3: Shared-tier p95 < 500ms (AC #6 — opt-in shared pool)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_shared_tier_p95_latency(config):
    """
    AC #6 measurement: shared-tier p95 < 500ms over 100 samples.
    Requires shared pool at steady-state (>= 5 idle pods per image).
    """
    token = _get_token(config)
    base_url = config["CONTROL_PLANE_URL"]

    latencies = []
    session_ids = []

    for i in range(SHARED_SAMPLE_COUNT):
        try:
            latency, session = await _create_session(base_url, token, low_latency=True)
            latencies.append(latency)
            sid = session.get("session_id") or session.get("id")
            if sid:
                session_ids.append(sid)
        except Exception as e:
            pytest.fail(f"Shared sample {i+1}/{SHARED_SAMPLE_COUNT} failed: {e}")

    # Cleanup
    cleanup_tasks = [_delete_session(base_url, token, sid) for sid in session_ids]
    await asyncio.gather(*cleanup_tasks, return_exceptions=True)

    latencies.sort()
    p95_idx = int(len(latencies) * 0.95)
    p95 = latencies[min(p95_idx, len(latencies) - 1)]

    assert p95 < SHARED_PATH_P95_THRESHOLD, (
        f"Shared-tier p95 latency {p95:.3f}s exceeds AC #6 threshold of {SHARED_PATH_P95_THRESHOLD}s.\n"
        f"min={latencies[0]:.3f}s, median={statistics.median(latencies):.3f}s, "
        f"p95={p95:.3f}s, max={latencies[-1]:.3f}s\n"
        f"Verify shared pool has >= 5 idle pods per image and low_latency=true is honoured."
    )
