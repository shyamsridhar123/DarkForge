"""Users router — per-user resource provisioning with propagation probe (plan S1 / Failure #3)."""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated

import httpx
from fastapi import APIRouter, Path, Request
from fastapi.responses import JSONResponse

from app.auth.dependencies import CurrentUser
from app.config import get_settings
from app.exceptions import PropagationTimeoutError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/users", tags=["users"])

PROPAGATION_PROBE_TIMEOUT_S = 90
PROPAGATION_POLL_INTERVAL_S = 5


async def _provision_arm_resources(oid: str) -> dict:
    """
    Call Azure Resource Manager to provision per-user Bicep resources.

    STUB: In production, POST to ARM REST API with the control-plane MI's
    contributor role on rg-opensandbox-users, deploying user.bicep.
    Returns { namespace, uami_client_id, kv_uri }.
    """
    settings = get_settings()
    namespace = f"ns-{oid}"
    short_oid = oid.replace("-", "")[:12]
    uami_client_id = f"stub-uami-{short_oid}"
    kv_uri = f"https://kv-user-{short_oid}.vault.azure.net/"

    logger.info(
        "ARM provisioning stub for oid=%s namespace=%s kv_uri=%s",
        oid, namespace, kv_uri,
    )
    # STUB: replace with actual ARM deployment call
    await asyncio.sleep(0)
    return {
        "namespace": namespace,
        "uami_client_id": uami_client_id,
        "kv_uri": kv_uri,
    }


async def _run_propagation_probe(
    namespace: str,
    uami_client_id: str,
    aks_token: str,
) -> bool:
    """
    Schedule a throwaway probe pod that verifies the UAMI federated credential
    has propagated to the AKS workload identity webhook.

    Plan: Failure #3 — synchronous propagation probe with 90-s bound.

    The probe pod runs:
      python -c "from azure.identity import DefaultAzureCredential; print(DefaultAzureCredential().get_token('https://management.azure.com/.default').token[:20])"

    Returns True on success, False on timeout.
    """
    settings = get_settings()
    probe_name = f"probe-wid-{namespace.replace('ns-', '')[:12]}"

    probe_manifest = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": probe_name,
            "namespace": namespace,
            "labels": {"app": "wid-propagation-probe"},
        },
        "spec": {
            "restartPolicy": "Never",
            "containers": [
                {
                    "name": "probe",
                    "image": f"{settings.acr_fqdn}/opensandbox/probe:latest",
                    "command": [
                        "python", "-c",
                        (
                            "from azure.identity import DefaultAzureCredential; "
                            "tok = DefaultAzureCredential().get_token('https://management.azure.com/.default').token; "
                            "print(tok[:20])"
                        ),
                    ],
                    "env": [
                        {"name": "AZURE_CLIENT_ID", "value": uami_client_id},
                    ],
                }
            ],
        },
    }

    # STUB: In production, create the probe pod via kubernetes_asyncio with aks_token,
    # poll pod phase until Succeeded/Failed, then delete it.
    logger.info(
        "Propagation probe stub for namespace=%s uami=%s (would poll for %ds)",
        namespace, uami_client_id, PROPAGATION_PROBE_TIMEOUT_S,
    )

    # Simulate successful propagation check
    await asyncio.sleep(0)
    return True


@router.post("/{oid}/provision")
async def provision_user(
    oid: Annotated[str, Path(description="User Entra object ID")],
    user: CurrentUser,
    request: Request,
) -> JSONResponse:
    """
    POST /users/{oid}/provision — create per-user namespace, UAMI, and Key Vault.

    Includes synchronous Workload Identity propagation probe (plan S1 / Failure #3).
    On propagation timeout: 503 with Retry-After: 90.
    """
    request.state.user_oid = user.oid

    # Only allow provisioning your own OID (or an admin role)
    if oid != user.oid and "Platform.Admin" not in user.roles:
        from app.exceptions import InsufficientScopeError
        raise InsufficientScopeError("You can only provision your own user resources")

    # ── 1. Provision ARM resources (UAMI + namespace + KV) ───────────────────
    provision_result = await _provision_arm_resources(oid)
    namespace = provision_result["namespace"]
    uami_client_id = provision_result["uami_client_id"]
    kv_uri = provision_result["kv_uri"]

    # ── 2. Synchronous propagation probe with 90-s timeout ───────────────────
    from app.auth.obo_exchange import exchange_for_aks
    aks_token = await exchange_for_aks(user._raw_token, user.oid)

    try:
        success = await asyncio.wait_for(
            _run_propagation_probe(namespace, uami_client_id, aks_token),
            timeout=PROPAGATION_PROBE_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        logger.error(
            "Workload Identity propagation probe timed out after %ds for oid=%s",
            PROPAGATION_PROBE_TIMEOUT_S, oid,
        )
        raise PropagationTimeoutError(
            "Workload Identity federated credential propagation timed out",
            detail=f"Retry after {PROPAGATION_PROBE_TIMEOUT_S}s; idempotent retry is safe",
        )

    if not success:
        raise PropagationTimeoutError(
            "Workload Identity propagation probe failed",
            detail="UAMI federated credential may not yet be active",
        )

    logger.info(
        "User provisioned oid=%s namespace=%s uami=%s kv=%s",
        oid, namespace, uami_client_id, kv_uri,
    )
    return JSONResponse({
        "namespace": namespace,
        "uami_client_id": uami_client_id,
        "kv_uri": kv_uri,
    })
