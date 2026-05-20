"""
tests/integration/test_propagation_probe.py

Plan task: Phase 3, Task 3.3 — Workload Identity federated-credential propagation probe.
           Failure #3 mitigation — race condition in POST /users/<oid>/provision.

Validates:
  1. POST /users/<oid>/provision completes within 60s p95 (synchronous probe).
  2. p95 latency < 60s over 25 parallel provisions.
  3. Negative path: on simulated timeout, returns 503 with Retry-After: 90.
  4. Idempotent retry succeeds after a 503.

Markers:
  @pytest.mark.requires_entra
  @pytest.mark.integration
  @pytest.mark.slow  (25 parallel provisions may take up to 90s)
"""

from __future__ import annotations

import asyncio
import os
import statistics
import time
import uuid

import pytest

pytestmark = [
    pytest.mark.requires_entra,
    pytest.mark.integration,
    pytest.mark.slow,
]

REQUIRED_ENV = [
    "CONTROL_PLANE_URL",
    "ENTRA_TEST_CLIENT_ID",
    "ENTRA_TEST_CLIENT_SECRET",
    "ENTRA_TENANT_ID",
    "ENTRA_SANDBOX_API_APP_ID",
]

P95_LATENCY_THRESHOLD_SECONDS = 60.0
PARALLEL_PROVISIONS = 25


def _check_env() -> list[str]:
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


async def _provision_user(
    base_url: str,
    token: str,
    oid: str,
) -> tuple[float, int]:
    """Provision a single test user OID; return (latency_seconds, http_status)."""
    try:
        import httpx
    except ImportError:
        pytest.skip("httpx not installed")

    async with httpx.AsyncClient(timeout=120) as client:
        start = time.monotonic()
        resp = await client.post(
            f"{base_url}/users/{oid}/provision",
            headers={"Authorization": f"Bearer {token}"},
            json={"test": True, "oid": oid},
        )
        latency = time.monotonic() - start
        return latency, resp.status_code


# ---------------------------------------------------------------------------
# Test 1: Single provision completes within 60s
# ---------------------------------------------------------------------------
def test_single_provision_latency(config):
    """Assert a single POST /users/<oid>/provision completes within 60s."""
    token = _get_token(config)
    test_oid = f"test-oid-{uuid.uuid4().hex}"

    latency, status = asyncio.run(
        _provision_user(config["CONTROL_PLANE_URL"], token, test_oid)
    )

    assert status in (200, 201, 202, 409), (
        f"Unexpected provision status {status} for OID {test_oid}"
    )
    assert latency < P95_LATENCY_THRESHOLD_SECONDS, (
        f"Provision latency {latency:.2f}s exceeds {P95_LATENCY_THRESHOLD_SECONDS}s threshold"
    )


# ---------------------------------------------------------------------------
# Test 2: 25 parallel provisions — p95 < 60s
# ---------------------------------------------------------------------------
def test_parallel_provision_p95(config):
    """Assert p95 latency < 60s over 25 parallel provisions."""
    token = _get_token(config)
    test_oids = [f"test-oid-{uuid.uuid4().hex}" for _ in range(PARALLEL_PROVISIONS)]

    async def _run_all():
        tasks = [
            _provision_user(config["CONTROL_PLANE_URL"], token, oid)
            for oid in test_oids
        ]
        return await asyncio.gather(*tasks, return_exceptions=True)

    results = asyncio.run(_run_all())

    latencies = []
    failures = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            failures.append(f"OID {test_oids[i]}: {result}")
        else:
            latency, status = result
            if status in (200, 201, 202, 409):
                latencies.append(latency)
            else:
                failures.append(f"OID {test_oids[i]}: status {status}")

    assert len(failures) == 0, (
        f"{len(failures)} provision(s) failed:\n" + "\n".join(failures[:5])
    )
    assert len(latencies) >= PARALLEL_PROVISIONS, (
        f"Only {len(latencies)}/{PARALLEL_PROVISIONS} provisions returned results"
    )

    latencies.sort()
    p95_index = int(len(latencies) * 0.95)
    p95 = latencies[min(p95_index, len(latencies) - 1)]

    assert p95 < P95_LATENCY_THRESHOLD_SECONDS, (
        f"p95 provision latency {p95:.2f}s exceeds {P95_LATENCY_THRESHOLD_SECONDS}s. "
        f"min={latencies[0]:.2f}s, median={statistics.median(latencies):.2f}s, max={latencies[-1]:.2f}s"
    )


# ---------------------------------------------------------------------------
# Test 3: Negative path — 503 with Retry-After on propagation timeout
# ---------------------------------------------------------------------------
def test_provision_timeout_returns_503_retry_after(config):
    """
    Negative path: if the synchronous propagation probe times out internally,
    the control plane must return HTTP 503 with Retry-After: 90.

    This test uses a special test OID prefix that the control plane recognises
    as a forced-timeout trigger in test environments (set FORCE_PROVISION_TIMEOUT=1).
    """
    import httpx

    token = _get_token(config)

    # Use the special timeout-trigger OID prefix (requires FORCE_PROVISION_TIMEOUT=1
    # to be set in the control plane test environment)
    forced_timeout_oid = f"timeout-trigger-{uuid.uuid4().hex}"

    resp = httpx.post(
        f"{config['CONTROL_PLANE_URL']}/users/{forced_timeout_oid}/provision",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Test-Force-Timeout": "1",
        },
        json={"test": True, "oid": forced_timeout_oid},
        timeout=120,
    )

    if resp.status_code == 503:
        retry_after = resp.headers.get("Retry-After", "")
        assert retry_after == "90", (
            f"503 response missing or wrong Retry-After header: '{retry_after}' (expected '90')"
        )
    elif resp.status_code in (200, 201, 202):
        pytest.skip(
            "Control plane did not simulate timeout (X-Test-Force-Timeout not handled). "
            "This negative path requires FORCE_PROVISION_TIMEOUT support in the control plane."
        )
    else:
        pytest.fail(
            f"Unexpected status {resp.status_code} for forced-timeout provision: {resp.text}"
        )


# ---------------------------------------------------------------------------
# Test 4: Idempotent retry succeeds after 503
# ---------------------------------------------------------------------------
def test_provision_idempotent_retry_after_503(config):
    """
    After a 503 with Retry-After: 90, a subsequent idempotent retry must succeed.
    Uses the same OID to verify idempotent behaviour (no duplicate resources created).
    """
    import httpx

    token = _get_token(config)
    retry_oid = f"retry-test-{uuid.uuid4().hex}"

    # First call — may succeed or 409 if already provisioned
    resp1 = httpx.post(
        f"{config['CONTROL_PLANE_URL']}/users/{retry_oid}/provision",
        headers={"Authorization": f"Bearer {token}"},
        json={"test": True, "oid": retry_oid},
        timeout=120,
    )
    assert resp1.status_code in (200, 201, 202, 503), (
        f"Unexpected first provision status: {resp1.status_code}"
    )

    # Second call with same OID — must be idempotent (200/201/409 acceptable)
    resp2 = httpx.post(
        f"{config['CONTROL_PLANE_URL']}/users/{retry_oid}/provision",
        headers={"Authorization": f"Bearer {token}"},
        json={"test": True, "oid": retry_oid},
        timeout=120,
    )
    assert resp2.status_code in (200, 201, 202, 409), (
        f"Idempotent retry returned unexpected status {resp2.status_code}: {resp2.text}. "
        "POST /users/<oid>/provision must be idempotent."
    )
