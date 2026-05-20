"""
tests/observability/test_audit_60s.py

Plan task: Phase 6, AC #12 — Audit log SLA: command appears in Log Analytics within 60s.

Validates:
  - Injects a known UUID command into a sandbox session.
  - Polls Log Analytics (via the fast-path: Diagnostic Settings → Event Hubs →
    Stream Analytics → Log Analytics).
  - Asserts the entry appears within 60 seconds of command execution.

References:
  - Critic B-C3 mitigation: audit path uses Event Hubs fast-path, not Container Insights
    (which has 2-10 min latency).
  - AC #12: "Audit log entry ... appears in Log Analytics queryable within 60s."

Markers:
  @pytest.mark.requires_entra
  @pytest.mark.observability
  @pytest.mark.slow
"""

from __future__ import annotations

import os
import time
import uuid

import pytest

pytestmark = [
    pytest.mark.requires_entra,
    pytest.mark.observability,
    pytest.mark.slow,
]

REQUIRED_ENV = [
    "CONTROL_PLANE_URL",
    "ENTRA_TEST_CLIENT_ID",
    "ENTRA_TEST_CLIENT_SECRET",
    "ENTRA_TENANT_ID",
    "ENTRA_SANDBOX_API_APP_ID",
    "LAW_WORKSPACE_ID",
]

AUDIT_SLA_SECONDS = 60
POLL_INTERVAL = 3


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


# ---------------------------------------------------------------------------
# Test: known UUID command appears in LAW within 60s
# ---------------------------------------------------------------------------
def test_audit_entry_within_60s(config):
    """
    AC #12: Inject a known UUID command; assert Log Analytics entry within 60s.

    Audit path: execd → Fluent Bit → Event Hubs → Stream Analytics → Log Analytics.
    NOT Container Insights (which has 2-10 min ingestion latency).
    """
    try:
        from azure.monitor.query import LogsQueryClient, LogsQueryStatus
        from azure.identity import DefaultAzureCredential
        import httpx
    except ImportError:
        pytest.skip("azure-monitor-query or httpx not installed")

    token = _get_token(config)
    base_url = config["CONTROL_PLANE_URL"]
    workspace_id = config["LAW_WORKSPACE_ID"]

    # Generate a unique sentinel UUID that will appear in the audit log
    audit_uuid = str(uuid.uuid4())
    audit_command = f"echo {audit_uuid}"

    # ---------------------------------------------------------------------------
    # Step 1: Create session
    # ---------------------------------------------------------------------------
    with httpx.Client(timeout=60) as client:
        session_resp = client.post(
            f"{base_url}/sessions",
            json={"image": "python312-sandbox", "low_latency": False},
            headers={"Authorization": f"Bearer {token}"},
        )
        session_resp.raise_for_status()
        session = session_resp.json()
        session_id = session.get("session_id") or session.get("id")
        assert session_id, f"No session_id: {session}"

        # ---------------------------------------------------------------------------
        # Step 2: Execute command containing the sentinel UUID
        # ---------------------------------------------------------------------------
        exec_time = time.time()
        exec_resp = client.post(
            f"{base_url}/sessions/{session_id}/execute",
            json={"code": audit_command, "language": "shell"},
            headers={"Authorization": f"Bearer {token}"},
        )
        exec_resp.raise_for_status()

    # ---------------------------------------------------------------------------
    # Step 3: Poll Log Analytics for the sentinel UUID
    # ---------------------------------------------------------------------------
    law_client = LogsQueryClient(DefaultAzureCredential())

    kql = f"""
    union
        (SandboxAuditLog | where Command contains "{audit_uuid}"),
        (ContainerLog | where LogEntry contains "{audit_uuid}"),
        (AppTraces | where Message contains "{audit_uuid}")
    | project TimeGenerated, Type, Command = coalesce(Command, LogEntry, Message)
    | order by TimeGenerated desc
    | take 1
    """

    deadline = exec_time + AUDIT_SLA_SECONDS
    found_at: float | None = None

    while time.time() < deadline:
        try:
            response = law_client.query_workspace(
                workspace_id=workspace_id,
                query=kql,
                timespan="PT5M",
            )
            if response.status == LogsQueryStatus.SUCCESS:
                for table in response.tables:
                    if table.rows:
                        found_at = time.time()
                        break
            if found_at:
                break
        except Exception:
            pass
        time.sleep(POLL_INTERVAL)

    # ---------------------------------------------------------------------------
    # Step 4: Cleanup
    # ---------------------------------------------------------------------------
    try:
        with httpx.Client(timeout=15) as client:
            client.delete(
                f"{base_url}/sessions/{session_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
    except Exception:
        pass

    # ---------------------------------------------------------------------------
    # Step 5: Assert SLA
    # ---------------------------------------------------------------------------
    assert found_at is not None, (
        f"AC #12 FAIL: Audit log entry for UUID '{audit_uuid}' NOT found in Log Analytics "
        f"within {AUDIT_SLA_SECONDS}s of command execution.\n"
        f"Verify the Diagnostic Settings → Event Hubs → Stream Analytics → LAW pipeline is active.\n"
        f"Do NOT use Container Insights for this path — it has 2-10 min latency."
    )

    elapsed = found_at - exec_time
    assert elapsed <= AUDIT_SLA_SECONDS, (
        f"AC #12 FAIL: Audit entry found after {elapsed:.1f}s (SLA = {AUDIT_SLA_SECONDS}s).\n"
        f"Fast-path audit pipeline is not meeting the 60s SLA."
    )

    # Report the actual latency
    print(
        f"\nAC #12 PASS: Audit entry for '{audit_uuid}' found after {elapsed:.1f}s "
        f"(SLA = {AUDIT_SLA_SECONDS}s)."
    )
