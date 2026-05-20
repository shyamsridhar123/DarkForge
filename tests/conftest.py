"""
tests/conftest.py

Shared fixtures for the OpenSandbox test suite.

Provides:
  - aks_client         kubernetes_asyncio CoreV1Api configured from KUBECONFIG / env
  - test_user_token    MSAL-acquired bearer token for test Entra tenant
  - acr_image_tag      Full ACR image reference for the current git SHA or env override
  - cleanup_test_resources  session-scoped async cleanup registry

Pytest markers registered here:
  requires_aks_kata   — tests that need a live AKS cluster with Kata enabled
  requires_entra      — tests that need Entra test tenant credentials
  slow                — tests that take > 30s (excluded from fast CI runs)
  security            — security PoC tests
  integration         — integration tests
  e2e                 — end-to-end tests
  observability       — observability / audit tests
"""

from __future__ import annotations

import asyncio
import os
from typing import AsyncGenerator, Generator

import pytest
import pytest_asyncio


# ---------------------------------------------------------------------------
# Register custom markers
# ---------------------------------------------------------------------------
def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "requires_aks_kata: mark test as requiring a live AKS cluster with Kata Pod Sandboxing enabled",
    )
    config.addinivalue_line(
        "markers",
        "requires_entra: mark test as requiring Entra test tenant credentials (env vars)",
    )
    config.addinivalue_line(
        "markers",
        "slow: mark test as slow (> 30s runtime); skipped in fast CI unless -m slow",
    )
    config.addinivalue_line(
        "markers",
        "security: mark test as a security PoC test",
    )
    config.addinivalue_line(
        "markers",
        "integration: mark test as an integration test",
    )
    config.addinivalue_line(
        "markers",
        "e2e: mark test as an end-to-end test",
    )
    config.addinivalue_line(
        "markers",
        "observability: mark test as an observability / audit pipeline test",
    )


# ---------------------------------------------------------------------------
# Event loop (module-scoped for async fixtures)
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def event_loop():
    """Session-scoped event loop for async fixtures."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ---------------------------------------------------------------------------
# AKS client fixture
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture(scope="session")
async def aks_client():
    """
    Returns a kubernetes_asyncio CoreV1Api client.
    Skips if neither KUBECONFIG nor in-cluster config is available.
    Requires @pytest.mark.requires_aks_kata to use directly.
    """
    try:
        from kubernetes_asyncio import client as k8s_client
        from kubernetes_asyncio import config as k8s_config
    except ImportError:
        pytest.skip("kubernetes_asyncio not installed")

    try:
        if os.environ.get("KUBECONFIG"):
            await k8s_config.load_kube_config(config_file=os.environ["KUBECONFIG"])
        elif os.environ.get("KUBERNETES_SERVICE_HOST"):
            await k8s_config.load_incluster_config()
        else:
            await k8s_config.load_kube_config()
    except Exception as e:
        pytest.skip(f"Kubernetes config not available: {e}")

    async with k8s_client.ApiClient() as api:
        yield k8s_client.CoreV1Api(api)


# ---------------------------------------------------------------------------
# Test user token fixture
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def test_user_token() -> str:
    """
    Acquires an MSAL bearer token for the test Entra tenant.
    Skips if required env vars are not set.
    """
    required = [
        "ENTRA_TENANT_ID",
        "ENTRA_TEST_CLIENT_ID",
        "ENTRA_TEST_CLIENT_SECRET",
        "ENTRA_SANDBOX_API_APP_ID",
    ]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        pytest.skip(f"Missing Entra env vars: {', '.join(missing)}")

    try:
        import msal
    except ImportError:
        pytest.skip("msal not installed")

    app = msal.ConfidentialClientApplication(
        client_id=os.environ["ENTRA_TEST_CLIENT_ID"],
        client_credential=os.environ["ENTRA_TEST_CLIENT_SECRET"],
        authority=f"https://login.microsoftonline.com/{os.environ['ENTRA_TENANT_ID']}",
    )
    result = app.acquire_token_for_client(
        scopes=[f"api://{os.environ['ENTRA_SANDBOX_API_APP_ID']}/.default"]
    )
    if "access_token" not in result:
        pytest.skip(
            f"Token acquisition failed: {result.get('error')} — {result.get('error_description')}"
        )
    return result["access_token"]


# ---------------------------------------------------------------------------
# ACR image tag fixture
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def acr_image_tag() -> str:
    """
    Returns the full ACR image reference for the current test run.
    Uses ACR_IMAGE_TAG env var if set; falls back to ACR_NAME + git SHA.
    """
    if os.environ.get("ACR_IMAGE_TAG"):
        return os.environ["ACR_IMAGE_TAG"]

    acr_name = os.environ.get("ACR_NAME")
    if not acr_name:
        pytest.skip("ACR_NAME env var not set — cannot resolve image tag")

    # Attempt to get git SHA
    git_sha = os.environ.get("GITHUB_SHA", "")
    if not git_sha:
        import subprocess
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=5
            )
            git_sha = result.stdout.strip()[:12]
        except Exception:
            git_sha = "latest"

    return f"{acr_name}.azurecr.io/python312-sandbox:{git_sha}"


# ---------------------------------------------------------------------------
# Cleanup registry fixture
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def cleanup_test_resources(aks_client) -> AsyncGenerator[list, None]:
    """
    A session-scoped async cleanup registry.

    Usage in tests:
        async def test_something(cleanup_test_resources):
            # Register a cleanup coroutine
            cleanup_test_resources.append(
                delete_namespaced_pod("my-pod", "my-ns")
            )

    All registered coroutines are awaited after the test, even on failure.
    """
    cleanups: list = []
    yield cleanups

    # Run all cleanups regardless of test outcome
    from kubernetes_asyncio.client import ApiException

    for coro in reversed(cleanups):
        try:
            await coro
        except ApiException as e:
            if e.status != 404:
                print(f"Cleanup warning: {e}")
        except Exception as e:
            print(f"Cleanup warning: {e}")


# ---------------------------------------------------------------------------
# Control plane URL fixture (convenience)
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def control_plane_url() -> str:
    url = os.environ.get("CONTROL_PLANE_URL")
    if not url:
        pytest.skip("CONTROL_PLANE_URL env var not set")
    return url.rstrip("/")


# ---------------------------------------------------------------------------
# Pytest addoption: --run-slow
# ---------------------------------------------------------------------------
def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-slow",
        action="store_true",
        default=False,
        help="Include slow tests (> 30s) in the test run",
    )
    parser.addoption(
        "--run-aks",
        action="store_true",
        default=False,
        help="Include tests that require a live AKS cluster with Kata",
    )
    parser.addoption(
        "--run-entra",
        action="store_true",
        default=False,
        help="Include tests that require Entra test tenant credentials",
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Skip gated tests unless the corresponding --run-* flag is passed."""
    skip_slow = pytest.mark.skip(reason="slow test — pass --run-slow to include")
    skip_aks = pytest.mark.skip(reason="requires AKS+Kata — pass --run-aks to include")
    skip_entra = pytest.mark.skip(reason="requires Entra — pass --run-entra to include")

    for item in items:
        if "slow" in item.keywords and not config.getoption("--run-slow"):
            item.add_marker(skip_slow)
        if "requires_aks_kata" in item.keywords and not config.getoption("--run-aks"):
            item.add_marker(skip_aks)
        if "requires_entra" in item.keywords and not config.getoption("--run-entra"):
            item.add_marker(skip_entra)
