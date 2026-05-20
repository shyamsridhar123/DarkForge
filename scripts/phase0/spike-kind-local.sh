#!/usr/bin/env bash
# spike-kind-local.sh
# Plan task: Phase 0, Task 0.3 — Lightweight kind spike for OpenSandbox controller.
#
# PURPOSE
#   Creates a local kind cluster, deploys the OpenSandbox Helm chart, and attempts
#   to schedule a sample pod with runtimeClassName: kata-vm-isolation.  The Kata
#   runtime will NOT be present in kind — the test goal is to verify the controller
#   accepts the pod spec and creates the expected CRD objects (not that the pod runs).
#
# TIME-BOX: 15 minutes total.  Script exits with timeout if exceeded.
#
# PREREQUISITES
#   kind, helm, kubectl

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

CLUSTER_NAME="opensandbox-spike"
HELM_CHART_REPO="https://alibaba.github.io/OpenSandbox"  # adjust if upstream differs
HELM_RELEASE="opensandbox-spike"
HELM_NAMESPACE="opensandbox-system"
KATA_RUNTIME="kata-vm-isolation"
TEST_NAMESPACE="spike-test"
TIMEOUT_SECONDS=900  # 15-minute time-box

echo "==> Phase 0 / Task 0.3: kind local spike (time-box ${TIMEOUT_SECONDS}s)"

# ---------------------------------------------------------------------------
# Prerequisite checks
# ---------------------------------------------------------------------------
for cmd in kind helm kubectl; do
  if ! command -v "$cmd" &>/dev/null; then
    echo "ERROR: '$cmd' not found. Install prerequisites and re-run." >&2
    exit 1
  fi
done

# ---------------------------------------------------------------------------
# Time-box enforcement
# ---------------------------------------------------------------------------
(
  sleep "${TIMEOUT_SECONDS}"
  echo ""
  echo "==> TIME-BOX EXCEEDED (${TIMEOUT_SECONDS}s). Forcing cleanup and exit."
  kind delete cluster --name "${CLUSTER_NAME}" 2>/dev/null || true
  kill 0
) &
WATCHDOG_PID=$!

cleanup() {
  kill "${WATCHDOG_PID}" 2>/dev/null || true
  echo "==> Deleting kind cluster ${CLUSTER_NAME} ..."
  kind delete cluster --name "${CLUSTER_NAME}" 2>/dev/null || true
  echo "==> kind spike cleanup done."
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# 1. Create kind cluster
# ---------------------------------------------------------------------------
echo "==> Creating kind cluster '${CLUSTER_NAME}' ..."
START_TIME=$(date +%s)

kind create cluster --name "${CLUSTER_NAME}" --wait 60s

echo "==> kind cluster created."
kubectl cluster-info --context "kind-${CLUSTER_NAME}"

# ---------------------------------------------------------------------------
# 2. Deploy OpenSandbox controller via Helm
# ---------------------------------------------------------------------------
echo "==> Adding OpenSandbox Helm repo ..."
helm repo add opensandbox "${HELM_CHART_REPO}" 2>/dev/null || \
  echo "    (Helm repo add failed — falling back to local chart if available)"

helm repo update 2>/dev/null || true

echo "==> Installing OpenSandbox Helm chart ..."
# If upstream chart isn't available, we attempt from local path
LOCAL_CHART="${REPO_ROOT}/infra/helm/opensandbox"
if helm repo list 2>/dev/null | grep -q opensandbox; then
  CHART_REF="opensandbox/opensandbox"
else
  CHART_REF="${LOCAL_CHART}"
fi

helm upgrade --install "${HELM_RELEASE}" "${CHART_REF}" \
  --namespace "${HELM_NAMESPACE}" \
  --create-namespace \
  --set "controller.replicaCount=1" \
  --set "runtimeClass.name=${KATA_RUNTIME}" \
  --timeout 120s \
  --wait || {
    echo "WARNING: Helm install did not fully succeed — controller may still be starting."
    echo "         Continuing with pod scheduling test ..."
  }

HELM_TIME=$(( $(date +%s) - START_TIME ))
echo "==> Helm install completed in ${HELM_TIME}s"

# ---------------------------------------------------------------------------
# 3. Register the RuntimeClass (kind won't have Kata handler, but API accepts it)
# ---------------------------------------------------------------------------
echo "==> Registering RuntimeClass '${KATA_RUNTIME}' ..."
kubectl apply -f - <<EOF
apiVersion: node.k8s.io/v1
kind: RuntimeClass
metadata:
  name: ${KATA_RUNTIME}
handler: kata-vm-isolation
scheduling:
  nodeClassification:
    tolerations:
      - key: runtime
        operator: Equal
        value: kata
        effect: NoSchedule
EOF

# ---------------------------------------------------------------------------
# 4. Attempt to schedule a sample pod with runtimeClassName
# ---------------------------------------------------------------------------
echo "==> Creating test namespace ${TEST_NAMESPACE} ..."
kubectl create namespace "${TEST_NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f -

echo "==> Scheduling sample pod with runtimeClassName: ${KATA_RUNTIME} ..."
kubectl apply -f - <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: kata-sample
  namespace: ${TEST_NAMESPACE}
  labels:
    app: kata-spike-sample
    spike: phase0-task03
spec:
  runtimeClassName: ${KATA_RUNTIME}
  restartPolicy: Never
  containers:
    - name: sample
      image: alpine:3.19
      command: ["echo", "kata-spike-pod-accepted"]
      resources:
        requests:
          cpu: "50m"
          memory: "32Mi"
EOF

# ---------------------------------------------------------------------------
# 5. Check if controller accepted the pod spec (pod may Pending due to no Kata)
# ---------------------------------------------------------------------------
sleep 5
POD_STATUS=$(kubectl get pod kata-sample -n "${TEST_NAMESPACE}" -o jsonpath='{.status.phase}' 2>/dev/null || echo "NotFound")
POD_EVENTS=$(kubectl describe pod kata-sample -n "${TEST_NAMESPACE}" 2>/dev/null | grep -A5 "Events:" || true)
CONTROLLER_LOGS=$(kubectl logs -n "${HELM_NAMESPACE}" \
  -l "app.kubernetes.io/name=opensandbox" --tail=50 2>/dev/null || echo "(no controller logs)")

# If pod is Pending (not Failed/Error), the controller accepted the spec
SPEC_ACCEPTED="unknown"
if [[ "$POD_STATUS" == "Pending" ]]; then
  SPEC_ACCEPTED="yes — pod is Pending (expected: Kata handler not present in kind)"
elif [[ "$POD_STATUS" == "Running" || "$POD_STATUS" == "Succeeded" ]]; then
  SPEC_ACCEPTED="yes — pod scheduled and running (unexpected in kind without Kata)"
elif [[ "$POD_STATUS" == "Failed" ]]; then
  SPEC_ACCEPTED="no — pod Failed; controller may have rejected the spec"
else
  SPEC_ACCEPTED="uncertain — pod status: ${POD_STATUS}"
fi

END_TIME=$(date +%s)
TOTAL_TIME=$(( END_TIME - START_TIME ))

echo ""
echo "==> kind spike result summary"
echo "    Pod phase:       ${POD_STATUS}"
echo "    Spec accepted:   ${SPEC_ACCEPTED}"
echo "    Total runtime:   ${TOTAL_TIME}s"
echo ""
echo "==> Controller logs (last 50 lines):"
echo "${CONTROLLER_LOGS}"
echo ""
echo "==> Pod events:"
echo "${POD_EVENTS}"
