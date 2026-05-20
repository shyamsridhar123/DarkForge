"""
tests/integration/test_ratify_admission.py

Plan task: Phase 2, Task 2.x — Ratify admission controller validation.

Validates:
  1. Pushing an UNSIGNED image to ACR and attempting to schedule it is DENIED.
  2. Pushing a SIGNED image to ACR and attempting to schedule it is ALLOWED.
  3. Admission denial error message is the correct Ratify error (not a generic 403).

Markers:
  @pytest.mark.requires_aks_kata
  @pytest.mark.integration
"""

from __future__ import annotations

import os
import subprocess
import time
import uuid

import pytest
from kubernetes_asyncio import client as k8s_client
from kubernetes_asyncio import config as k8s_config
from kubernetes_asyncio.client import ApiException

pytestmark = [
    pytest.mark.requires_aks_kata,
    pytest.mark.integration,
]

REQUIRED_ENV = [
    "ACR_NAME",
    "KV_NAME",
    "NOTATION_CERT_PRIMARY_ID",   # Full KV cert ID for primary Notation cert
]

RATIFY_NAMESPACE = "ratify-admission-test"
KATA_RUNTIME_CLASS = "kata-vm-isolation"
UNSIGNED_IMAGE_TAG = f"admission-test-unsigned-{uuid.uuid4().hex[:8]}"
SIGNED_IMAGE_TAG = f"admission-test-signed-{uuid.uuid4().hex[:8]}"
BASE_IMAGE = "alpine:3.19"
POD_TIMEOUT = 90


def _check_env() -> list[str]:
    return [k for k in REQUIRED_ENV if not os.environ.get(k)]


@pytest.fixture(scope="module")
def config():
    missing = _check_env()
    if missing:
        pytest.skip(f"Missing env vars: {', '.join(missing)}")
    return {k: os.environ[k] for k in REQUIRED_ENV}


@pytest.fixture(scope="module")
def k8s_core(event_loop):
    import asyncio

    async def _setup():
        if os.environ.get("KUBECONFIG"):
            await k8s_config.load_kube_config(config_file=os.environ["KUBECONFIG"])
        else:
            await k8s_config.load_kube_config()
        api = k8s_client.ApiClient()
        return k8s_client.CoreV1Api(api)

    return event_loop.run_until_complete(_setup())


@pytest.fixture(scope="module", autouse=True)
def admission_test_namespace(k8s_core):
    import asyncio

    async def _create():
        ns = k8s_client.V1Namespace(
            metadata=k8s_client.V1ObjectMeta(name=RATIFY_NAMESPACE)
        )
        try:
            await k8s_core.create_namespace(ns)
        except ApiException as e:
            if e.status != 409:
                raise

    asyncio.get_event_loop().run_until_complete(_create())
    yield

    async def _delete():
        try:
            await k8s_core.delete_namespace(
                RATIFY_NAMESPACE,
                body=k8s_client.V1DeleteOptions(grace_period_seconds=0),
            )
        except ApiException:
            pass

    asyncio.get_event_loop().run_until_complete(_delete())


def _build_and_push_image(acr_name: str, tag: str) -> str:
    """
    Build a trivial test image and push to ACR.
    Returns the full image reference.
    """
    full_ref = f"{acr_name}.azurecr.io/admission-test:{tag}"
    # Build: reuse existing alpine image retag
    subprocess.run(
        ["docker", "pull", BASE_IMAGE],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["docker", "tag", BASE_IMAGE, full_ref],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["docker", "push", full_ref],
        check=True, capture_output=True,
    )
    return full_ref


def _sign_image(image_ref: str, kv_cert_id: str) -> None:
    """Sign an image reference using Notation + Azure Key Vault plugin."""
    subprocess.run(
        [
            "notation", "sign",
            "--signature-format", "cose",
            "--plugin", "azure-kv",
            "--id", kv_cert_id,
            image_ref,
        ],
        check=True, capture_output=True,
    )


async def _try_schedule_pod(
    core: k8s_client.CoreV1Api,
    namespace: str,
    pod_name: str,
    image_ref: str,
) -> tuple[bool, str]:
    """
    Attempt to create a pod. Returns (scheduled: bool, error_message: str).
    A Ratify denial will surface as a 403 ApiException with a Ratify error body.
    """
    pod = k8s_client.V1Pod(
        metadata=k8s_client.V1ObjectMeta(name=pod_name, namespace=namespace),
        spec=k8s_client.V1PodSpec(
            runtime_class_name=KATA_RUNTIME_CLASS,
            restart_policy="Never",
            containers=[
                k8s_client.V1Container(
                    name="test",
                    image=image_ref,
                    command=["echo", "admission-test"],
                )
            ],
        ),
    )
    try:
        await core.create_namespaced_pod(namespace=namespace, body=pod)
        return True, ""
    except ApiException as e:
        return False, e.body or str(e)


# ---------------------------------------------------------------------------
# Test 1: Unsigned image is DENIED by Ratify admission
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_unsigned_image_denied(config, k8s_core):
    """Assert that scheduling an unsigned image fails with Ratify admission error."""
    acr_name = config["ACR_NAME"]
    image_ref = _build_and_push_image(acr_name, UNSIGNED_IMAGE_TAG)
    pod_name = f"unsigned-{uuid.uuid4().hex[:8]}"

    scheduled, error_body = await _try_schedule_pod(
        k8s_core, RATIFY_NAMESPACE, pod_name, image_ref
    )

    assert not scheduled, (
        f"CRITICAL: Unsigned image '{image_ref}' was scheduled successfully. "
        "Ratify admission control is not enforcing signature verification."
    )

    # Verify the error message is from Ratify (not a generic 403)
    assert any(marker in error_body.lower() for marker in [
        "ratify",
        "signature",
        "not signed",
        "verification failed",
        "no signatures",
        "admission webhook",
    ]), (
        f"Admission was denied but not by Ratify. Error body: {error_body[:500]}"
    )


# ---------------------------------------------------------------------------
# Test 2: Signed image is ALLOWED by Ratify admission
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_signed_image_admitted(config, k8s_core):
    """Assert that scheduling a Notation-signed image succeeds."""
    acr_name = config["ACR_NAME"]
    kv_cert_id = config["NOTATION_CERT_PRIMARY_ID"]

    image_ref = _build_and_push_image(acr_name, SIGNED_IMAGE_TAG)
    _sign_image(image_ref, kv_cert_id)

    pod_name = f"signed-{uuid.uuid4().hex[:8]}"
    scheduled, error_body = await _try_schedule_pod(
        k8s_core, RATIFY_NAMESPACE, pod_name, image_ref
    )

    assert scheduled, (
        f"Signed image '{image_ref}' was DENIED by Ratify. "
        f"Notation signing or TrustPolicy configuration may be incorrect.\n"
        f"Error: {error_body[:500]}"
    )

    # Cleanup pod
    try:
        await k8s_core.delete_namespaced_pod(
            name=pod_name,
            namespace=RATIFY_NAMESPACE,
            body=k8s_client.V1DeleteOptions(grace_period_seconds=0),
        )
    except ApiException:
        pass
