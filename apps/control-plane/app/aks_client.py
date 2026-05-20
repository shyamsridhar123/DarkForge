"""kubernetes_asyncio wrapper — creates pods with user OBO token so AKS audit logs show user OID."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from kubernetes_asyncio import client as k8s_client
from kubernetes_asyncio.client import ApiClient, ApiException

from app.auth.dependencies import UserClaims
from app.auth.obo_exchange import exchange_for_aks
from app.config import get_settings
from app.exceptions import AKSClientError, RateLimitError

logger = logging.getLogger(__name__)

# In-memory counters for warm-pool rate limiting (replace with Redis in production)
_warm_pool_user_counts: dict[str, int] = {}
_warm_pool_platform_count: int = 0

KATA_RUNTIME_CLASS = "kata-vm-isolation"
SANDBOX_LABEL_APP = "opensandbox"


def _make_api_client(bearer_token: str) -> ApiClient:
    """Build a kubernetes_asyncio ApiClient authenticated with the user's OBO token.

    This ensures AKS audit logs record the user's identity (OID/UPN),
    not the control-plane app identity.
    """
    settings = get_settings()
    configuration = k8s_client.Configuration()
    configuration.host = f"https://{settings.aks_api_fqdn}"
    configuration.ssl_ca_cert = settings.aks_ca_bundle_path
    configuration.api_key = {"authorization": f"Bearer {bearer_token}"}
    configuration.api_key_prefix = {"authorization": ""}
    return ApiClient(configuration=configuration)


def _sandbox_pod_manifest(
    name: str,
    namespace: str,
    image: str,
    user_oid: str,
    session_id: str,
    env: dict[str, str],
) -> dict[str, Any]:
    """Build a Pod manifest with Kata runtime, non-root, no privileged."""
    env_vars = [{"name": k, "value": v} for k, v in env.items()]
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": {
                "app": SANDBOX_LABEL_APP,
                "session-id": session_id,
                "user-oid": user_oid,
                "identity-tier": "per-user",
            },
            "annotations": {
                "opensandbox.io/user-oid": user_oid,
                "opensandbox.io/session-id": session_id,
            },
        },
        "spec": {
            "runtimeClassName": KATA_RUNTIME_CLASS,
            "automountServiceAccountToken": True,
            "securityContext": {
                "runAsNonRoot": True,
                "runAsUser": 65534,
                "seccompProfile": {"type": "RuntimeDefault"},
            },
            "containers": [
                {
                    "name": "sandbox",
                    "image": image,
                    "env": env_vars,
                    "securityContext": {
                        "allowPrivilegeEscalation": False,
                        "privileged": False,
                        "readOnlyRootFilesystem": True,
                        "capabilities": {"drop": ["ALL"]},
                    },
                    "resources": {
                        "requests": {"cpu": "100m", "memory": "256Mi"},
                        "limits": {"cpu": "2", "memory": "2Gi"},
                    },
                }
            ],
            "restartPolicy": "Never",
        },
    }


async def create_sandbox_pod(
    user_claims: UserClaims,
    image: str,
    low_latency: bool,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """
    Create a sandbox pod.

    low_latency=False (default): fresh pod in ns-<user-oid> with per-user Kata isolation.
    low_latency=True: bind a warm-pool pod in ns-warm-pool-<image-tag> (rate-limited).
    """
    global _warm_pool_platform_count
    settings = get_settings()
    env = env or {}

    # Exchange for AKS-scoped token (OBO — scope is AKS server app, not management.azure.com)
    aks_token = await exchange_for_aks(user_claims._raw_token, user_claims.oid)

    session_id = str(uuid.uuid4())

    if low_latency:
        return await _bind_warm_pool_pod(
            user_claims=user_claims,
            image=image,
            session_id=session_id,
            aks_token=aks_token,
            env=env,
        )

    # ── Default path: fresh pod in per-user namespace ─────────────────────────
    namespace = f"{settings.sandbox_namespace_prefix}-{user_claims.oid}"
    pod_name = f"sandbox-{session_id[:8]}"
    manifest = _sandbox_pod_manifest(
        name=pod_name,
        namespace=namespace,
        image=image,
        user_oid=user_claims.oid,
        session_id=session_id,
        env=env,
    )

    async with _make_api_client(aks_token) as api_client:
        core_v1 = k8s_client.CoreV1Api(api_client)
        try:
            await core_v1.create_namespaced_pod(namespace=namespace, body=manifest)
        except ApiException as exc:
            logger.error("AKS create pod failed: status=%s body=%s", exc.status, exc.body)
            raise AKSClientError(f"Failed to create sandbox pod: {exc.reason}") from exc

    logger.info(
        "Created sandbox pod name=%s namespace=%s session_id=%s user_oid=%s",
        pod_name, namespace, session_id, user_claims.oid,
    )
    return {
        "session_id": session_id,
        "pod_name": pod_name,
        "namespace": namespace,
        "identity_tier": "per-user",
        "connection_info": {"pod": pod_name, "namespace": namespace},
    }


async def _bind_warm_pool_pod(
    user_claims: UserClaims,
    image: str,
    session_id: str,
    aks_token: str,
    env: dict[str, str],
) -> dict[str, Any]:
    """Bind an idle warm-pool pod to a user session (rate-limited)."""
    global _warm_pool_platform_count
    settings = get_settings()

    # ── Rate limit checks ─────────────────────────────────────────────────────
    user_count = _warm_pool_user_counts.get(user_claims.oid, 0)
    if user_count >= settings.warm_pool_rate_limit_per_user:
        raise RateLimitError(
            f"Per-user warm-pool limit of {settings.warm_pool_rate_limit_per_user} reached"
        )
    if _warm_pool_platform_count >= settings.warm_pool_platform_limit:
        raise RateLimitError(
            f"Platform warm-pool limit of {settings.warm_pool_platform_limit} reached"
        )

    # ── Find an idle warm-pool pod ────────────────────────────────────────────
    image_tag = image.rsplit(":", 1)[-1] if ":" in image else "latest"
    warm_ns = f"{settings.warm_pool_namespace_prefix}-{image_tag}"

    async with _make_api_client(aks_token) as api_client:
        core_v1 = k8s_client.CoreV1Api(api_client)
        try:
            pods = await core_v1.list_namespaced_pod(
                namespace=warm_ns,
                label_selector="opensandbox.io/warm-pool-state=idle",
            )
        except ApiException as exc:
            raise AKSClientError(f"Failed to list warm-pool pods: {exc.reason}") from exc

        if not pods.items:
            raise AKSClientError("No idle warm-pool pods available; retry or use default tier")

        target_pod = pods.items[0]
        pod_name = target_pod.metadata.name

        # ── Patch pod labels to assign to user ────────────────────────────────
        patch = {
            "metadata": {
                "labels": {
                    "opensandbox.io/warm-pool-state": "bound",
                    "user-oid": user_claims.oid,
                    "session-id": session_id,
                    "identity-tier": "shared_warm_pool",
                }
            }
        }
        try:
            await core_v1.patch_namespaced_pod(
                name=pod_name, namespace=warm_ns, body=patch
            )
        except ApiException as exc:
            raise AKSClientError(f"Failed to bind warm-pool pod: {exc.reason}") from exc

    # Update in-memory counters
    _warm_pool_user_counts[user_claims.oid] = user_count + 1
    _warm_pool_platform_count += 1

    logger.info(
        "Bound warm-pool pod name=%s ns=%s session_id=%s user_oid=%s identity_tier=shared_warm_pool",
        pod_name, warm_ns, session_id, user_claims.oid,
    )
    return {
        "session_id": session_id,
        "pod_name": pod_name,
        "namespace": warm_ns,
        "identity_tier": "shared_warm_pool",
        "connection_info": {"pod": pod_name, "namespace": warm_ns},
    }


async def exec_in_sandbox(
    session_id: str,
    namespace: str,
    pod_name: str,
    command: str,
    timeout_s: int,
    user_claims: UserClaims,
) -> dict[str, Any]:
    """
    Execute a command in a sandbox pod via execd's HTTP endpoint.

    NOTE: Uses execd HTTP API (not kubectl exec) per the OpenSandbox upstream design.
    This is a stub — wire to the actual execd endpoint in Phase 4.
    """
    # STUB: In production, POST to execd's sidecar HTTP endpoint inside the pod
    # e.g. http://<pod-ip>:8888/exec  with {"command": command, "timeout": timeout_s}
    logger.info(
        "exec_in_sandbox session_id=%s pod=%s/%s command_len=%d user_oid=%s",
        session_id, namespace, pod_name, len(command), user_claims.oid,
    )
    return {
        "stdout": "",
        "stderr": "",
        "exit_code": 0,
        "session_id": session_id,
    }


async def get_sandbox_pod(
    namespace: str,
    pod_name: str,
    user_claims: UserClaims,
) -> dict[str, Any]:
    """Return pod metadata for a sandbox session."""
    aks_token = await exchange_for_aks(user_claims._raw_token, user_claims.oid)
    async with _make_api_client(aks_token) as api_client:
        core_v1 = k8s_client.CoreV1Api(api_client)
        try:
            pod = await core_v1.read_namespaced_pod(name=pod_name, namespace=namespace)
        except ApiException as exc:
            if exc.status == 404:
                return {}
            raise AKSClientError(f"Failed to get pod: {exc.reason}") from exc
    return {
        "name": pod.metadata.name,
        "namespace": pod.metadata.namespace,
        "phase": pod.status.phase if pod.status else "Unknown",
        "labels": pod.metadata.labels or {},
    }


async def delete_sandbox_pod(
    namespace: str,
    pod_name: str,
    user_claims: UserClaims,
    identity_tier: str = "per-user",
) -> None:
    """Terminate a sandbox pod and release any warm-pool slot."""
    global _warm_pool_platform_count
    aks_token = await exchange_for_aks(user_claims._raw_token, user_claims.oid)
    async with _make_api_client(aks_token) as api_client:
        core_v1 = k8s_client.CoreV1Api(api_client)
        try:
            await core_v1.delete_namespaced_pod(name=pod_name, namespace=namespace)
        except ApiException as exc:
            if exc.status != 404:
                raise AKSClientError(f"Failed to delete pod: {exc.reason}") from exc

    if identity_tier == "shared_warm_pool":
        _warm_pool_user_counts[user_claims.oid] = max(
            0, _warm_pool_user_counts.get(user_claims.oid, 0) - 1
        )
        _warm_pool_platform_count = max(0, _warm_pool_platform_count - 1)

    logger.info(
        "Deleted pod name=%s namespace=%s identity_tier=%s user_oid=%s",
        pod_name, namespace, identity_tier, user_claims.oid,
    )
