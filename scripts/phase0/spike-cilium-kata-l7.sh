#!/usr/bin/env bash
# spike-cilium-kata-l7.sh
# Plan task: Phase 0, Task 0.4 — Validate Cilium ACNS L7 NetworkPolicy on Kata pods.
#
# PURPOSE
#   Applies a minimal Kata pod + CiliumNetworkPolicy (L7 FQDN allow/deny) to a live
#   AKS dev cluster, verifies egress enforcement, captures Hubble evidence, and
#   writes results to docs/integration-spikes.md.
#
# PREREQUISITES
#   - kubectl configured against dev AKS cluster with Kata + Cilium ACNS enabled
#   - cilium CLI (for hubble relay) OR hubble CLI
#   - az CLI (for AKS credential retrieval if needed)
#
# OUTPUTS
#   docs/integration-spikes.md  PASS/FAIL verdict + Hubble evidence + recommendation

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
OUT_FILE="${REPO_ROOT}/docs/integration-spikes.md"

NAMESPACE="phase0-kata-l7"
POD_NAME="kata-l7-probe"
KATA_RUNTIME="kata-vm-isolation"
CURL_IMAGE="curlimages/curl:8.6.0"
TIMEOUT_POD_READY=120   # seconds
TIMEOUT_CURL=15         # seconds per curl call

ALLOWED_FQDN="pypi.org"
BLOCKED_FQDN="github.com"

echo "==> Phase 0 / Task 0.4: Cilium ACNS L7-on-Kata spike"

# ---------------------------------------------------------------------------
# Prerequisite checks
# ---------------------------------------------------------------------------
for cmd in kubectl; do
  if ! command -v "$cmd" &>/dev/null; then
    echo "ERROR: '$cmd' not found in PATH. Install it and ensure kubectl context is set." >&2
    exit 1
  fi
done

HUBBLE_AVAILABLE=false
if command -v hubble &>/dev/null; then
  HUBBLE_AVAILABLE=true
fi

CILIUM_CLI_AVAILABLE=false
if command -v cilium &>/dev/null; then
  CILIUM_CLI_AVAILABLE=true
fi

# ---------------------------------------------------------------------------
# Cleanup function (always runs)
# ---------------------------------------------------------------------------
cleanup() {
  echo "==> Cleaning up namespace ${NAMESPACE} ..."
  kubectl delete namespace "${NAMESPACE}" --ignore-not-found=true --timeout=60s || true
  echo "==> Cleanup done."
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# 1. Create namespace + apply manifests
# ---------------------------------------------------------------------------
echo "==> Creating namespace ${NAMESPACE} ..."
kubectl create namespace "${NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f -

echo "==> Applying CiliumNetworkPolicy ..."
kubectl apply -f - <<EOF
apiVersion: "cilium.io/v2"
kind: CiliumNetworkPolicy
metadata:
  name: kata-l7-egress
  namespace: ${NAMESPACE}
spec:
  endpointSelector:
    matchLabels:
      app: kata-l7-probe
  egress:
    # Allow DNS resolution (required for FQDN policy to function)
    - toEndpoints:
        - matchLabels:
            io.kubernetes.pod.namespace: kube-system
            k8s-app: kube-dns
      toPorts:
        - ports:
            - port: "53"
              protocol: UDP
          rules:
            dns:
              - matchPattern: "*"
    # Allow HTTPS to pypi.org only
    - toFQDNs:
        - matchPattern: "${ALLOWED_FQDN}"
      toPorts:
        - ports:
            - port: "443"
              protocol: TCP
EOF

echo "==> Applying Kata pod ..."
kubectl apply -f - <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: ${POD_NAME}
  namespace: ${NAMESPACE}
  labels:
    app: kata-l7-probe
spec:
  runtimeClassName: ${KATA_RUNTIME}
  restartPolicy: Never
  containers:
    - name: probe
      image: ${CURL_IMAGE}
      command: ["sleep", "3600"]
      resources:
        requests:
          cpu: "100m"
          memory: "64Mi"
        limits:
          cpu: "500m"
          memory: "128Mi"
      securityContext:
        allowPrivilegeEscalation: false
        runAsNonRoot: true
        runAsUser: 1000
        readOnlyRootFilesystem: true
        capabilities:
          drop: ["ALL"]
EOF

# ---------------------------------------------------------------------------
# 2. Wait for pod Ready
# ---------------------------------------------------------------------------
echo "==> Waiting for pod ${POD_NAME} to be Ready (timeout ${TIMEOUT_POD_READY}s) ..."
kubectl wait pod "${POD_NAME}" \
  --namespace="${NAMESPACE}" \
  --for=condition=Ready \
  --timeout="${TIMEOUT_POD_READY}s"

# ---------------------------------------------------------------------------
# 3. Run egress tests
# ---------------------------------------------------------------------------
ALLOWED_RESULT="UNKNOWN"
BLOCKED_RESULT="UNKNOWN"
ALLOWED_OUTPUT=""
BLOCKED_OUTPUT=""

echo "==> Testing ALLOWED destination: https://${ALLOWED_FQDN}/simple/"
if kubectl exec "${POD_NAME}" --namespace="${NAMESPACE}" -- \
    curl -sSf --max-time "${TIMEOUT_CURL}" "https://${ALLOWED_FQDN}/simple/" -o /dev/null 2>&1; then
  ALLOWED_RESULT="PASS"
  echo "    ✓ ${ALLOWED_FQDN} reachable (expected)"
else
  ALLOWED_RESULT="FAIL"
  ALLOWED_OUTPUT=$(kubectl exec "${POD_NAME}" --namespace="${NAMESPACE}" -- \
    curl -v --max-time "${TIMEOUT_CURL}" "https://${ALLOWED_FQDN}/simple/" 2>&1 || true)
  echo "    ✗ ${ALLOWED_FQDN} unreachable — L7 policy may not be working correctly"
fi

echo "==> Testing BLOCKED destination: https://${BLOCKED_FQDN}/"
BLOCKED_HTTP_CODE=""
if BLOCKED_OUTPUT=$(kubectl exec "${POD_NAME}" --namespace="${NAMESPACE}" -- \
    curl -sv --max-time "${TIMEOUT_CURL}" "https://${BLOCKED_FQDN}/" 2>&1); then
  BLOCKED_RESULT="FAIL"  # Should NOT succeed
  echo "    ✗ ${BLOCKED_FQDN} was reachable — policy NOT enforced"
else
  BLOCKED_RESULT="PASS"
  echo "    ✓ ${BLOCKED_FQDN} blocked (expected)"
fi

# ---------------------------------------------------------------------------
# 4. Collect Hubble flow evidence
# ---------------------------------------------------------------------------
HUBBLE_EVIDENCE="(hubble CLI not available — install hubble and re-run for flow IDs)"
if [[ "$HUBBLE_AVAILABLE" == "true" ]]; then
  echo "==> Collecting Hubble flow logs for namespace ${NAMESPACE} ..."
  HUBBLE_EVIDENCE=$(hubble observe \
    --namespace "${NAMESPACE}" \
    --last 50 \
    --output json 2>/dev/null | \
    python3 -c "
import sys, json
flows = []
for line in sys.stdin:
    try:
        f = json.loads(line)
        src = f.get('source', {})
        dst = f.get('destination', {})
        verdict = f.get('verdict', '')
        reason = f.get('drop_reason_desc', f.get('drop_reason', ''))
        l7 = f.get('l7', {})
        flows.append({
            'flow_id': f.get('node_name','?') + ':' + str(f.get('time','')),
            'src': src.get('namespace','?') + '/' + src.get('pod_name','?'),
            'dst': dst.get('namespace','?') + '/' + dst.get('pod_name','?') + ' ' + str(dst.get('identity','')),
            'verdict': verdict,
            'reason': reason,
            'l7': l7,
        })
    except Exception:
        pass
for fl in flows[-20:]:
    print(json.dumps(fl))
" 2>/dev/null || echo "(hubble observe failed — is hubble relay running?)")
elif [[ "$CILIUM_CLI_AVAILABLE" == "true" ]]; then
  echo "==> Collecting Cilium flow logs via cilium CLI ..."
  HUBBLE_EVIDENCE=$(cilium hubble port-forward &
  PF_PID=$!
  sleep 3
  hubble observe --namespace "${NAMESPACE}" --last 50 --output json 2>/dev/null | head -40 || true
  kill $PF_PID 2>/dev/null || true)
fi

# ---------------------------------------------------------------------------
# 5. Determine overall result and recommendation
# ---------------------------------------------------------------------------
if [[ "$ALLOWED_RESULT" == "PASS" && "$BLOCKED_RESULT" == "PASS" ]]; then
  OVERALL="PASS"
  RECOMMENDATION="standard"
  SUMMARY_MSG="Both allow (${ALLOWED_FQDN}) and deny (${BLOCKED_FQDN}) policies enforced correctly at L7 on Kata pods."
elif [[ "$ALLOWED_RESULT" == "FAIL" && "$BLOCKED_RESULT" == "PASS" ]]; then
  OVERALL="PARTIAL-FAIL"
  RECOMMENDATION="premium"
  SUMMARY_MSG="Denied correctly but allowed destination was also blocked — check FQDN DNS resolution inside Kata or Cilium ACNS version."
elif [[ "$ALLOWED_RESULT" == "PASS" && "$BLOCKED_RESULT" == "FAIL" ]]; then
  OVERALL="FAIL"
  RECOMMENDATION="premium"
  SUMMARY_MSG="Allowed destination reachable but blocked destination also reachable — L7 policy NOT enforced on Kata pods. Upgrade Azure Firewall to Premium SKU per Task 1.5 fallback."
else
  OVERALL="FAIL"
  RECOMMENDATION="premium"
  SUMMARY_MSG="Both tests failed — CiliumNetworkPolicy not functioning on Kata. Upgrade to Azure Firewall Premium per Task 1.5."
fi

# ---------------------------------------------------------------------------
# 6. Write docs/integration-spikes.md
# ---------------------------------------------------------------------------
mkdir -p "$(dirname "${OUT_FILE}")"

{
  echo "# Integration Spikes — Phase 0 Results"
  echo ""
  echo "> Generated by \`scripts/phase0/spike-cilium-kata-l7.sh\`"
  echo "> Plan task: Phase 0, Task 0.4"
  echo "> Date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "> AKS context: $(kubectl config current-context 2>/dev/null || echo 'unknown')"
  echo ""
  echo "## Cilium L7 on Kata"
  echo ""
  echo "**Result:** \`${OVERALL}\`"
  echo ""
  echo "**Allowed destination (\`${ALLOWED_FQDN}\`):** ${ALLOWED_RESULT}"
  echo ""
  echo "**Blocked destination (\`${BLOCKED_FQDN}\`):** ${BLOCKED_RESULT}"
  echo ""
  echo "**Summary:** ${SUMMARY_MSG}"
  echo ""
  echo "## Evidence: Hubble flow IDs / error messages"
  echo ""
  echo "\`\`\`"
  echo "${HUBBLE_EVIDENCE}"
  if [[ -n "${ALLOWED_OUTPUT}" ]]; then
    echo ""
    echo "--- Allowed-destination curl output (failure) ---"
    echo "${ALLOWED_OUTPUT}"
  fi
  if [[ "$BLOCKED_RESULT" == "FAIL" && -n "${BLOCKED_OUTPUT}" ]]; then
    echo ""
    echo "--- Blocked-destination curl output (unexpected success) ---"
    echo "${BLOCKED_OUTPUT}" | head -30
  fi
  echo "\`\`\`"
  echo ""
  echo "## Recommended \`egressEnforcementTier\`"
  echo ""
  echo "\`${RECOMMENDATION}\`"
  echo ""
  echo "| Value | Meaning |"
  echo "|-------|---------|"
  echo "| \`standard\` | Cilium ACNS L7 FQDN policy works on Kata — use Azure Firewall Standard as L3/L4 backstop |"
  echo "| \`premium\` | Cilium L7 ineffective on Kata — upgrade Azure Firewall to Premium SKU for SNI-based HTTPS filtering |"
  echo ""
  echo "## Architecture decision (per Task 1.5)"
  echo ""
  if [[ "$RECOMMENDATION" == "standard" ]]; then
    echo "Phase 0 task 0.4 **PASSED**. Proceed with Cilium ACNS L7 as primary egress enforcer."
    echo "Set Bicep parameter \`egressEnforcementTier = 'standard'\`."
    echo "AC #17 verification: Cilium L7 deny for non-allowlisted FQDNs."
  else
    echo "Phase 0 task 0.4 **FAILED**. Upgrade Azure Firewall to Premium SKU."
    echo "Set Bicep parameter \`egressEnforcementTier = 'premium'\`."
    echo "AC #17 verification: Azure Firewall Premium SNI-based deny for non-allowlisted HTTPS FQDNs."
    echo ""
    echo "> ⚠️ TLS-MITM is NOT viable inside Kata pods (no CA distribution to untrusted containers)."
    echo "> Use HTTP application rules (host-header) for HTTP, and SNI inspection for HTTPS."
    echo "> This covers pypi.org, npmjs.org etc. but not arbitrary HTTPS — document in AC #17."
  fi
} > "${OUT_FILE}"

echo ""
echo "==> Wrote findings to: ${OUT_FILE}"
echo "==> Cilium L7 on Kata: ${OVERALL}"
echo "==> Recommended egressEnforcementTier: ${RECOMMENDATION}"
