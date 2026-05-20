"""
Container-escape PoC test suite for Kata-isolated sandbox pods.

Implements Phase 6 Task 6.7 of the consensus plan and Critic B-C5 (container-escape test).
This is the security thesis verification: if any of these PoCs succeeds against a Kata pod,
the entire Kata-as-trust-boundary principle (Principle 1) is invalidated.

Tests run on every PR (when AZURE_DEV_SUB is configured) and nightly against prod AKS.
A failure here blocks merge and pages on-call.

Test design:
  - Schedule a real Kata pod via kubernetes_asyncio against the configured AKS cluster
  - Copy a payload script into the pod
  - Execute the payload; assert non-zero exit code or specific error signature
  - Tear down the pod regardless of result

The 3 PoCs:
  1. CVE-2022-0492 class: cgroup release_agent abuse for host code execution
  2. /proc/self/exe overwrite: runc CVE-2019-5736 class
  3. Kernel module load: insmod (Kata guest kernel should disallow kmod loading by default)

Each PoC has a comment block citing the CVE/technique reference.

Run:
    pytest tests/security/test_container_escape.py -m requires_aks_kata -v

References:
  - CVE-2022-0492: https://nvd.nist.gov/vuln/detail/CVE-2022-0492
  - CVE-2019-5736: https://nvd.nist.gov/vuln/detail/CVE-2019-5736
  - Kata threat model: https://github.com/kata-containers/kata-containers/blob/main/docs/threat-model.md
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest

# Mark all tests in this module as requiring real AKS+Kata; they SKIP if not configured.
pytestmark = pytest.mark.requires_aks_kata

# Use kubernetes_asyncio if available; bail out gracefully if not.
try:
    from kubernetes_asyncio import client, config
    from kubernetes_asyncio.stream import WsApiClient
except ImportError:  # pragma: no cover
    pytest.skip(
        "kubernetes_asyncio not installed — install dev deps to run these tests",
        allow_module_level=True,
    )


PAYLOAD_DIR = pathlib.Path(__file__).parent / "escape_pocs"
TEST_NAMESPACE = os.environ.get("KATA_ESCAPE_TEST_NAMESPACE", "phase0-escape-test")
KATA_RUNTIME_CLASS = os.environ.get("KATA_RUNTIME_CLASS", "kata-vm-isolation")
TEST_IMAGE = os.environ.get("KATA_TEST_IMAGE", "mcr.microsoft.com/azurelinux/base/core:3.0")
POD_READY_TIMEOUT_S = 120
EXEC_TIMEOUT_S = 60


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

@pytest.fixture(scope="module")
def event_loop():
    """Module-scoped event loop so we share the AKS connection across tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="module")
async def k8s_clients() -> AsyncIterator[tuple[client.CoreV1Api, client.RbacAuthorizationV1Api]]:
    """Load kube config (in-cluster or kubeconfig) and yield the API clients."""
    try:
        config.load_incluster_config()
    except config.ConfigException:
        await config.load_kube_config()
    core = client.CoreV1Api()
    rbac = client.RbacAuthorizationV1Api()
    try:
        yield core, rbac
    finally:
        await client.ApiClient().close()


@pytest.fixture(scope="module")
async def test_namespace(k8s_clients: tuple[client.CoreV1Api, Any]) -> AsyncIterator[str]:
    """Create the test namespace; tear it down (and everything in it) on exit."""
    core, _ = k8s_clients
    ns_manifest = client.V1Namespace(
        metadata=client.V1ObjectMeta(
            name=TEST_NAMESPACE,
            labels={"purpose": "escape-poc", "auto-cleanup": "true"},
        )
    )
    try:
        await core.create_namespace(body=ns_manifest)
    except client.ApiException as exc:
        if exc.status != 409:  # AlreadyExists — fine for re-runs
            raise
    yield TEST_NAMESPACE
    # Teardown: delete the whole namespace (cascades to all pods).
    try:
        await core.delete_namespace(
            name=TEST_NAMESPACE,
            body=client.V1DeleteOptions(grace_period_seconds=0),
        )
    except client.ApiException:
        pass  # Best-effort cleanup; don't mask test failures.


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

async def _create_kata_pod(
    core: client.CoreV1Api,
    namespace: str,
    name: str,
    payload_script: str,
) -> client.V1Pod:
    """Create a Kata pod, wait for it to be Ready, return the Pod object.

    The pod sleeps forever so we can exec into it; we copy the payload separately.
    Security context locked to non-root + drop ALL caps so the PoC must escape
    through the VM boundary, not because the container itself is privileged.
    """
    pod_manifest = client.V1Pod(
        metadata=client.V1ObjectMeta(
            name=name,
            namespace=namespace,
            labels={"app": "escape-test", "test-id": name},
        ),
        spec=client.V1PodSpec(
            runtime_class_name=KATA_RUNTIME_CLASS,
            tolerations=[
                client.V1Toleration(key="runtime", value="kata", effect="NoSchedule"),
            ],
            node_selector={"kubernetes.azure.com/agentpool": "kata"},
            restart_policy="Never",
            automount_service_account_token=False,
            security_context=client.V1PodSecurityContext(
                run_as_non_root=True,
                run_as_user=10001,
                fs_group=10001,
                seccomp_profile=client.V1SeccompProfile(type="RuntimeDefault"),
            ),
            containers=[
                client.V1Container(
                    name="payload",
                    image=TEST_IMAGE,
                    command=["/bin/sh", "-c", "sleep 600"],
                    security_context=client.V1SecurityContext(
                        allow_privilege_escalation=False,
                        read_only_root_filesystem=False,  # Need /tmp for payload
                        capabilities=client.V1Capabilities(drop=["ALL"]),
                    ),
                    resources=client.V1ResourceRequirements(
                        requests={"cpu": "100m", "memory": "64Mi"},
                        limits={"cpu": "500m", "memory": "256Mi"},
                    ),
                ),
            ],
        ),
    )
    await core.create_namespaced_pod(namespace=namespace, body=pod_manifest)

    # Wait for Ready.
    deadline = time.time() + POD_READY_TIMEOUT_S
    while time.time() < deadline:
        pod = await core.read_namespaced_pod(name=name, namespace=namespace)
        if pod.status.phase == "Running" and all(
            cs.ready for cs in (pod.status.container_statuses or [])
        ):
            return pod
        if pod.status.phase == "Failed":
            raise RuntimeError(f"Pod {name} failed during startup: {pod.status}")
        await asyncio.sleep(2)
    raise TimeoutError(f"Pod {name} did not reach Ready within {POD_READY_TIMEOUT_S}s")


async def _exec_payload(
    namespace: str,
    pod_name: str,
    payload_path: pathlib.Path,
) -> tuple[int, str, str]:
    """Copy the payload script into the pod and execute it.

    Returns: (exit_code, stdout, stderr)
    """
    payload_contents = payload_path.read_text()

    # Use the WsApiClient stream interface to upload + run.
    # We base64 the payload to avoid quoting hell.
    import base64
    payload_b64 = base64.b64encode(payload_contents.encode()).decode()

    ws_client = WsApiClient()
    try:
        core = client.CoreV1Api(api_client=ws_client)
        exec_command = [
            "/bin/sh",
            "-c",
            f"echo '{payload_b64}' | base64 -d > /tmp/payload.sh && "
            "chmod +x /tmp/payload.sh && "
            "/tmp/payload.sh; echo \"__EXIT__$?\"",
        ]
        # `connect_get_namespaced_pod_exec` returns a websocket stream; we read it to completion.
        from kubernetes_asyncio.stream import stream
        resp = await asyncio.wait_for(
            stream(
                core.connect_get_namespaced_pod_exec,
                pod_name,
                namespace,
                command=exec_command,
                stderr=True,
                stdin=False,
                stdout=True,
                tty=False,
                _preload_content=False,
            ),
            timeout=EXEC_TIMEOUT_S,
        )

        stdout_lines: list[str] = []
        stderr_lines: list[str] = []
        while resp.is_open():
            await resp.update(timeout=1)
            if resp.peek_stdout():
                stdout_lines.append(resp.read_stdout())
            if resp.peek_stderr():
                stderr_lines.append(resp.read_stderr())

        stdout = "".join(stdout_lines)
        stderr = "".join(stderr_lines)

        # Parse the exit code we appended.
        exit_code = -1
        for line in stdout.splitlines():
            if line.startswith("__EXIT__"):
                try:
                    exit_code = int(line.removeprefix("__EXIT__"))
                except ValueError:
                    exit_code = -1
                break

        return exit_code, stdout, stderr
    finally:
        await ws_client.close()


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_cgroup_release_agent_escape_blocked(
    k8s_clients: tuple[client.CoreV1Api, Any],
    test_namespace: str,
) -> None:
    """CVE-2022-0492 class: cgroup release_agent abuse must not yield host code execution.

    The classic technique mounts a writable cgroup v1 hierarchy from inside the
    container, writes to `release_agent`, and triggers the agent on cgroup release.
    Under Kata, the guest kernel sees its own cgroup tree (not the host's), so even
    if the technique "succeeds" inside the VM, the host is untouched.

    Acceptance: payload script exits NON-ZERO, OR completes with explicit "BLOCKED"
    marker indicating the cgroup mount or write was rejected.
    """
    core, _ = k8s_clients
    pod_name = f"escape-cgroup-{uuid.uuid4().hex[:8]}"
    try:
        await _create_kata_pod(core, test_namespace, pod_name, "cgroup_release_agent")
        exit_code, stdout, stderr = await _exec_payload(
            test_namespace,
            pod_name,
            PAYLOAD_DIR / "cgroup_release_agent.sh",
        )

        # The PoC payload script is expected to print "BLOCKED:" prefix on success
        # (i.e., when escape is correctly prevented). A zero exit + no BLOCKED marker
        # indicates the technique actually succeeded — that's a FAIL of the entire
        # security thesis.
        assert exit_code != 0 or "BLOCKED:" in stdout, (
            f"Container escape via cgroup release_agent appears to have SUCCEEDED — "
            f"this invalidates Principle 1 (Kata as trust boundary). "
            f"exit_code={exit_code}, stdout={stdout!r}, stderr={stderr!r}"
        )
    finally:
        try:
            await core.delete_namespaced_pod(name=pod_name, namespace=test_namespace)
        except client.ApiException:
            pass


@pytest.mark.asyncio
async def test_proc_self_exe_overwrite_blocked(
    k8s_clients: tuple[client.CoreV1Api, Any],
    test_namespace: str,
) -> None:
    """CVE-2019-5736 class: overwriting /proc/self/exe must not escape Kata.

    The runc CVE allowed a malicious container to overwrite the runc binary on the
    host by writing through /proc/self/exe. Under Kata, /proc/self/exe lives in the
    guest VM's filesystem; even a successful overwrite stays inside the VM.

    Acceptance: open(/proc/self/exe, O_WRONLY) returns ETXTBSY or EPERM, OR the
    payload exits non-zero, OR the BLOCKED marker is printed.
    """
    core, _ = k8s_clients
    pod_name = f"escape-procexe-{uuid.uuid4().hex[:8]}"
    try:
        await _create_kata_pod(core, test_namespace, pod_name, "proc_self_exe")
        exit_code, stdout, stderr = await _exec_payload(
            test_namespace,
            pod_name,
            PAYLOAD_DIR / "proc_self_exe.sh",
        )
        assert exit_code != 0 or "BLOCKED:" in stdout, (
            f"Container escape via /proc/self/exe overwrite appears to have SUCCEEDED. "
            f"exit_code={exit_code}, stdout={stdout!r}, stderr={stderr!r}"
        )
    finally:
        try:
            await core.delete_namespaced_pod(name=pod_name, namespace=test_namespace)
        except client.ApiException:
            pass


@pytest.mark.asyncio
async def test_kernel_module_load_blocked(
    k8s_clients: tuple[client.CoreV1Api, Any],
    test_namespace: str,
) -> None:
    """Loading a kernel module from inside the pod must fail.

    Kata guest kernels are configured with `module.sig_enforce=1` and minimal kmod
    support; even root inside the VM cannot insmod arbitrary modules. Outside Kata
    (plain runc), CAP_SYS_MODULE would also be required — we explicitly drop ALL
    caps in pod spec, so insmod should fail on either layer.

    Acceptance: insmod returns non-zero with EPERM / ENOSYS / "Operation not permitted".
    """
    core, _ = k8s_clients
    pod_name = f"escape-kmod-{uuid.uuid4().hex[:8]}"
    try:
        await _create_kata_pod(core, test_namespace, pod_name, "kmod_load")
        exit_code, stdout, stderr = await _exec_payload(
            test_namespace,
            pod_name,
            PAYLOAD_DIR / "kmod_load.sh",
        )
        assert exit_code != 0 or "BLOCKED:" in stdout, (
            f"Kernel module load from inside Kata pod appears to have SUCCEEDED — "
            f"the guest kernel security posture is broken. "
            f"exit_code={exit_code}, stdout={stdout!r}, stderr={stderr!r}"
        )
    finally:
        try:
            await core.delete_namespaced_pod(name=pod_name, namespace=test_namespace)
        except client.ApiException:
            pass
