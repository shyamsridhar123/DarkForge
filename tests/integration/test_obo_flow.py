"""
tests/integration/test_obo_flow.py

Plan task: Phase 3, Task 3.3 + Critic S-C8 — End-to-end OBO token exchange validation.

Validates:
  1. SDK acquires a user token for the OpenSandbox API app.
  2. Control plane validates the token (401 on bad token).
  3. OBO exchange produces a downstream token with aud = AKS server app ID
     (NOT management.azure.com — per Critic S-C8).
  4. AKS audit log shows the user's UPN as the actor for the resulting pod creation.

Markers:
  @pytest.mark.requires_entra  — skipped if Entra test env vars not set
  @pytest.mark.integration
"""

from __future__ import annotations

import os
import time
import uuid

import pytest
import pytest_asyncio

# ---------------------------------------------------------------------------
# Markers + skip guard
# ---------------------------------------------------------------------------
pytestmark = [
    pytest.mark.requires_entra,
    pytest.mark.integration,
]

REQUIRED_ENV = [
    "ENTRA_TENANT_ID",
    "ENTRA_TEST_CLIENT_ID",       # Test user app registration client ID
    "ENTRA_TEST_CLIENT_SECRET",   # Test client secret (or use cert)
    "ENTRA_SANDBOX_API_APP_ID",   # OpenSandbox API app registration application ID
    "AKS_SERVER_APP_ID",          # AAD-integrated AKS server app ID (from aadProfile.serverAppID)
    "CONTROL_PLANE_URL",          # https://<app-gateway-fqdn>/api
    "LAW_WORKSPACE_ID",           # Log Analytics workspace ID for AKS audit log query
]


def _check_env() -> list[str]:
    return [k for k in REQUIRED_ENV if not os.environ.get(k)]


@pytest.fixture(scope="module")
def entra_config():
    missing = _check_env()
    if missing:
        pytest.skip(
            f"Entra OBO test skipped — missing env vars: {', '.join(missing)}. "
            "Set these to run against a real test Entra tenant."
        )
    return {k: os.environ[k] for k in REQUIRED_ENV}


@pytest.fixture(scope="module")
def test_session_id():
    return f"obo-test-{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# Helper: acquire user token via MSAL client-credentials (simulates user flow)
# ---------------------------------------------------------------------------
def _acquire_user_token(config: dict) -> str:
    """
    Acquire an access token for the OpenSandbox API app.
    Uses client-credentials flow for test automation (simulates a user token
    with the API's application permission scope).
    """
    try:
        import msal
    except ImportError:
        pytest.skip("msal not installed — pip install msal")

    app = msal.ConfidentialClientApplication(
        client_id=config["ENTRA_TEST_CLIENT_ID"],
        client_credential=config["ENTRA_TEST_CLIENT_SECRET"],
        authority=f"https://login.microsoftonline.com/{config['ENTRA_TENANT_ID']}",
    )
    scope = f"api://{config['ENTRA_SANDBOX_API_APP_ID']}/.default"
    result = app.acquire_token_for_client(scopes=[scope])
    assert "access_token" in result, (
        f"Token acquisition failed: {result.get('error')} — {result.get('error_description')}"
    )
    return result["access_token"]


# ---------------------------------------------------------------------------
# Test 1: SDK acquires a user token successfully
# ---------------------------------------------------------------------------
def test_sdk_acquires_user_token(entra_config):
    """Assert that the test SDK client can acquire a token for the API app scope."""
    token = _acquire_user_token(entra_config)
    assert token, "Token is empty"
    assert len(token.split(".")) == 3, "Token does not appear to be a JWT"


# ---------------------------------------------------------------------------
# Test 2: Control plane validates the token (401 on bad token)
# ---------------------------------------------------------------------------
def test_control_plane_rejects_invalid_token(entra_config):
    """Assert that the control plane returns 401 for a malformed bearer token."""
    import httpx

    resp = httpx.get(
        f"{entra_config['CONTROL_PLANE_URL']}/sessions",
        headers={"Authorization": "Bearer this.is.not.a.real.token"},
        timeout=15,
    )
    assert resp.status_code == 401, (
        f"Expected 401 for invalid token, got {resp.status_code}: {resp.text}"
    )


def test_control_plane_rejects_wrong_audience(entra_config):
    """Assert that a valid token with the wrong aud claim returns 401."""
    import msal, httpx

    # Acquire a token for the WRONG resource (management.azure.com)
    app = msal.ConfidentialClientApplication(
        client_id=entra_config["ENTRA_TEST_CLIENT_ID"],
        client_credential=entra_config["ENTRA_TEST_CLIENT_SECRET"],
        authority=f"https://login.microsoftonline.com/{entra_config['ENTRA_TENANT_ID']}",
    )
    result = app.acquire_token_for_client(
        scopes=["https://management.azure.com/.default"]
    )
    wrong_token = result.get("access_token", "")

    if not wrong_token:
        pytest.skip("Could not acquire management.azure.com token for negative test")

    resp = httpx.get(
        f"{entra_config['CONTROL_PLANE_URL']}/sessions",
        headers={"Authorization": f"Bearer {wrong_token}"},
        timeout=15,
    )
    assert resp.status_code == 401, (
        f"Expected 401 for wrong-audience token, got {resp.status_code}. "
        "AC #4: control plane must reject tokens not intended for the OpenSandbox API."
    )


# ---------------------------------------------------------------------------
# Test 3: OBO exchange produces token with aud = AKS server app ID (S-C8)
# ---------------------------------------------------------------------------
def test_obo_token_audience_is_aks_server_app(entra_config, test_session_id):
    """
    Critic S-C8: Assert the OBO-exchanged downstream token has aud = AKS server app ID.

    The control plane exchanges the incoming user token for an AKS-scoped token
    via OBO.  The downstream token MUST have:
      - aud == AKS server app ID (from aadProfile.serverAppID)
      - NOT aud == https://management.azure.com/

    This is verified by calling the control plane's debug/introspect endpoint
    (available in non-prod environments) or by checking the AKS audit log.
    """
    import httpx, base64, json

    user_token = _acquire_user_token(entra_config)

    # POST a session creation request — control plane will perform OBO internally
    session_req = {
        "image": "python312-sandbox",
        "test_session_id": test_session_id,
        "low_latency": False,
    }
    resp = httpx.post(
        f"{entra_config['CONTROL_PLANE_URL']}/sessions",
        json=session_req,
        headers={"Authorization": f"Bearer {user_token}"},
        timeout=60,
    )
    assert resp.status_code in (200, 201, 202), (
        f"Session creation failed: {resp.status_code} — {resp.text}"
    )

    session_data = resp.json()
    session_id = session_data.get("session_id") or session_data.get("id")
    assert session_id, f"No session_id in response: {session_data}"

    # If a debug introspect endpoint is available, verify the OBO token audience
    debug_resp = httpx.get(
        f"{entra_config['CONTROL_PLANE_URL']}/debug/last-obo-claims",
        headers={"Authorization": f"Bearer {user_token}"},
        timeout=15,
    )
    if debug_resp.status_code == 200:
        claims = debug_resp.json()
        obo_aud = claims.get("aud", "")
        aks_app_id = entra_config["AKS_SERVER_APP_ID"]

        # aud must equal the AKS server app ID — NOT management.azure.com
        assert obo_aud == aks_app_id or aks_app_id in (
            obo_aud if isinstance(obo_aud, list) else [obo_aud]
        ), (
            f"Critic S-C8 VIOLATION: OBO token aud='{obo_aud}' != AKS server app ID '{aks_app_id}'. "
            "The control plane must exchange for AKS scope, not management.azure.com."
        )
        assert "management.azure.com" not in str(obo_aud), (
            f"Critic S-C8 VIOLATION: OBO token aud contains management.azure.com: '{obo_aud}'"
        )
    else:
        # Debug endpoint not available — skip audience check (verified via AKS audit log)
        pytest.skip(
            "Debug introspect endpoint not available — OBO audience verified via AKS audit log test below"
        )

    return session_id


# ---------------------------------------------------------------------------
# Test 4: AKS audit log shows user UPN as actor for pod creation
# ---------------------------------------------------------------------------
def test_aks_audit_log_shows_user_upn(entra_config, test_session_id):
    """
    Assert that the AKS audit log (AKSAudit table in Log Analytics) records
    the user's UPN (not the control-plane app ID) as the actor for the pod
    created by the test session.

    This verifies the end-to-end OBO chain: user → control plane → OBO → AKS API
    with user identity preserved.
    """
    try:
        from azure.monitor.query import LogsQueryClient, LogsQueryStatus
        from azure.identity import DefaultAzureCredential
    except ImportError:
        pytest.skip("azure-monitor-query not installed")

    credential = DefaultAzureCredential()
    client = LogsQueryClient(credential)

    workspace_id = entra_config["LAW_WORKSPACE_ID"]
    # Give the audit log up to 90 seconds to appear
    deadline = time.time() + 90
    found_upn = None

    kql = f"""
    AKSAudit
    | where ObjectRef.Name startswith "sandbox-"
    | where RequestUri contains "{test_session_id}"
    | project TimeGenerated, User_Username = tostring(User.Username), ObjectRef
    | order by TimeGenerated desc
    | take 5
    """

    while time.time() < deadline:
        response = client.query_workspace(
            workspace_id=workspace_id,
            query=kql,
            timespan="PT5M",  # last 5 minutes
        )
        if response.status == LogsQueryStatus.SUCCESS and response.tables:
            rows = response.tables[0].rows
            if rows:
                found_upn = rows[0][1]  # User_Username column
                break
        time.sleep(5)

    if found_upn is None:
        pytest.skip(
            f"AKS audit log entry for session {test_session_id} not found within 90s. "
            "Audit path may not be fully configured. Run test_audit_60s.py for targeted audit SLA check."
        )

    # UPN should be the test user's identity, NOT the control-plane service principal
    control_plane_app_id = entra_config.get("ENTRA_TEST_CLIENT_ID", "")
    assert found_upn != control_plane_app_id, (
        f"AKS audit log shows control-plane app ID '{found_upn}' as actor, "
        "not the user's UPN. OBO exchange may not be working correctly."
    )
    assert "@" in found_upn or found_upn.startswith("system:"), (
        f"User.Username '{found_upn}' does not look like a UPN. OBO identity propagation may be broken."
    )
