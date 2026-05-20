#!/usr/bin/env bash
# spike-opensandbox-crd.sh
# Plan task: Phase 0, Task 0.1 — Validate OpenSandbox CRD scope + RBAC requirements.
#
# PURPOSE
#   Clone the upstream OpenSandbox repository and statically analyse its manifests
#   and Go source for CRD definitions, ClusterRole requirements, and runtimeClassName
#   references.  Writes findings to docs/upstream-delta.md.
#
# OUTPUTS
#   docs/upstream-delta.md   Structured summary of CRDs, ClusterRoles, and RBAC scope.
#
# PREREQUISITES
#   git, grep (or ripgrep), kubectl (just for 'kubectl api-resources' docs reference — no
#   live cluster required for this spike).

set -euo pipefail

REPO_URL="https://github.com/alibaba/OpenSandbox"
TMPDIR="$(mktemp -d)"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
OUT_FILE="${REPO_ROOT}/docs/upstream-delta.md"

echo "==> Phase 0 / Task 0.1: OpenSandbox CRD + RBAC spike"
echo "    Cloning ${REPO_URL} into ${TMPDIR} ..."
git clone --depth 1 "${REPO_URL}" "${TMPDIR}/opensandbox" 2>&1 | tail -3

CLONE_DIR="${TMPDIR}/opensandbox"

# ---------------------------------------------------------------------------
# 1. Find all CRD manifests
# ---------------------------------------------------------------------------
echo "==> Scanning for CustomResourceDefinition ..."
CRD_FILES=()
while IFS= read -r f; do
  CRD_FILES+=("$f")
done < <(grep -rl "CustomResourceDefinition" "${CLONE_DIR}" --include="*.yaml" --include="*.yml" 2>/dev/null || true)

declare -A CRD_NAMES
declare -A CRD_APIVERSIONS
declare -A CRD_SCOPES

for f in "${CRD_FILES[@]}"; do
  # Extract name, apiVersion and scope from each CRD file
  # Use awk to parse simple YAML fields — sufficient for structured CRD manifests.
  while IFS= read -r line; do
    if [[ "$line" =~ ^[[:space:]]*name:[[:space:]]+(.*) ]]; then
      _name="${BASH_REMATCH[1]}"
    fi
    if [[ "$line" =~ ^apiVersion:[[:space:]]+(.*) ]]; then
      _apiversion="${BASH_REMATCH[1]}"
    fi
    if [[ "$line" =~ ^[[:space:]]*scope:[[:space:]]+(.*) ]]; then
      _scope="${BASH_REMATCH[1]}"
    fi
  done < "$f"
  # Only capture lines from files that actually contain CRD kind
  if grep -q "kind: CustomResourceDefinition" "$f" 2>/dev/null; then
    _key="${_name:-unknown}"
    CRD_NAMES["$_key"]="${_name:-}"
    CRD_APIVERSIONS["$_key"]="${_apiversion:-}"
    CRD_SCOPES["$_key"]="${_scope:-}"
  fi
done

# ---------------------------------------------------------------------------
# 2. Find apiextensions.k8s.io references (confirms real CRD machinery usage)
# ---------------------------------------------------------------------------
echo "==> Scanning for apiextensions.k8s.io ..."
APIEXT_REFS=$(grep -r "apiextensions.k8s.io" "${CLONE_DIR}" --include="*.go" --include="*.yaml" --include="*.yml" -l 2>/dev/null || true)

# ---------------------------------------------------------------------------
# 3. Find ClusterRole + ClusterRoleBinding manifests
# ---------------------------------------------------------------------------
echo "==> Scanning for ClusterRole / ClusterRoleBinding ..."
CLUSTERROLE_FILES=$(grep -rl "ClusterRole" "${CLONE_DIR}" --include="*.yaml" --include="*.yml" 2>/dev/null || true)

declare -a CLUSTERROLE_NAMES=()
while IFS= read -r f; do
  [[ -z "$f" ]] && continue
  while IFS= read -r line; do
    if [[ "$line" =~ ^[[:space:]]*name:[[:space:]]+(.*) ]]; then
      CLUSTERROLE_NAMES+=("${BASH_REMATCH[1]}")
    fi
  done < <(grep -A2 "kind: ClusterRole" "$f" 2>/dev/null || true)
done <<< "${CLUSTERROLE_FILES}"

# De-duplicate
readarray -t CLUSTERROLE_NAMES < <(printf '%s\n' "${CLUSTERROLE_NAMES[@]}" | sort -u)

# ---------------------------------------------------------------------------
# 4. Check rbac.authorization.k8s.io references in Go source
# ---------------------------------------------------------------------------
echo "==> Scanning for rbac.authorization.k8s.io in Go source ..."
RBAC_GO_REFS=$(grep -rl "rbac.authorization.k8s.io" "${CLONE_DIR}" --include="*.go" 2>/dev/null || true)

# ---------------------------------------------------------------------------
# 5. Determine cluster-admin requirement
# ---------------------------------------------------------------------------
CLUSTER_ADMIN_REFS=$(grep -r "cluster-admin" "${CLONE_DIR}" --include="*.yaml" --include="*.yml" 2>/dev/null | wc -l | tr -d ' ')
if [[ "$CLUSTER_ADMIN_REFS" -gt 0 ]]; then
  CLUSTER_ADMIN_REQUIRED="yes (${CLUSTER_ADMIN_REFS} references found)"
else
  # Check Go source too
  CLUSTER_ADMIN_GO=$(grep -r "cluster-admin" "${CLONE_DIR}" --include="*.go" 2>/dev/null | wc -l | tr -d ' ')
  if [[ "$CLUSTER_ADMIN_GO" -gt 0 ]]; then
    CLUSTER_ADMIN_REQUIRED="uncertain (only found in Go source: ${CLUSTER_ADMIN_GO} refs)"
  else
    CLUSTER_ADMIN_REQUIRED="no"
  fi
fi

# ---------------------------------------------------------------------------
# 6. runtimeClassName references
# ---------------------------------------------------------------------------
echo "==> Scanning for runtimeClassName references ..."
RUNTIME_CLASS_REFS=$(grep -r "runtimeClassName" "${CLONE_DIR}" --include="*.go" --include="*.yaml" --include="*.yml" 2>/dev/null | head -40 || true)
RUNTIME_CLASS_COUNT=$(echo "${RUNTIME_CLASS_REFS}" | grep -c "runtimeClassName" || true)
if [[ "$RUNTIME_CLASS_COUNT" -gt 0 ]]; then
  RUNTIME_OPINIONATED="yes — controller references runtimeClassName in ${RUNTIME_CLASS_COUNT} location(s)"
  # Extract the specific runtimeClass names if hardcoded
  RUNTIME_VALUES=$(grep -r "runtimeClassName" "${CLONE_DIR}" --include="*.go" --include="*.yaml" --include="*.yml" 2>/dev/null \
    | grep -oP 'runtimeClassName[:\s]+["'"'"']?\K[^"'"'"'\s]+' | sort -u || echo "(none extracted — check manually)")
else
  RUNTIME_OPINIONATED="no — no direct runtimeClassName references found (may delegate to sandbox spec)"
  RUNTIME_VALUES="n/a"
fi

# ---------------------------------------------------------------------------
# 7. Recommended RBAC scope
# ---------------------------------------------------------------------------
if [[ "$CLUSTER_ADMIN_REQUIRED" == "no" ]] && [[ "${#CLUSTERROLE_NAMES[@]}" -le 2 ]]; then
  RECOMMENDED_RBAC="namespace — minimal ClusterRoles; controller appears confineable"
elif [[ "$CLUSTER_ADMIN_REQUIRED" == yes* ]]; then
  RECOMMENDED_RBAC="cluster — cluster-admin binding required; consider patching upstream to narrow scope"
else
  RECOMMENDED_RBAC="uncertain — manual review of ClusterRole rules required before production deploy"
fi

# ---------------------------------------------------------------------------
# 8. Write docs/upstream-delta.md
# ---------------------------------------------------------------------------
mkdir -p "$(dirname "${OUT_FILE}")"

{
  echo "# OpenSandbox Upstream Delta — CRD + RBAC Analysis"
  echo ""
  echo "> Generated by \`scripts/phase0/spike-opensandbox-crd.sh\`"
  echo "> Plan task: Phase 0, Task 0.1"
  echo "> Date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "> Upstream commit: $(git -C "${CLONE_DIR}" rev-parse HEAD 2>/dev/null || echo 'unknown')"
  echo ""
  echo "## CRDs"
  echo ""
  if [[ "${#CRD_NAMES[@]}" -eq 0 ]]; then
    echo "- (none found — CRDs may be bundled in Helm chart or applied separately)"
  else
    for k in "${!CRD_NAMES[@]}"; do
      echo "- **Name:** \`${CRD_NAMES[$k]}\`  **apiVersion:** \`${CRD_APIVERSIONS[$k]:-unknown}\`  **Scope:** \`${CRD_SCOPES[$k]:-unknown}\`"
    done
  fi
  echo ""
  echo "## ClusterRoles required"
  echo ""
  if [[ "${#CLUSTERROLE_NAMES[@]}" -eq 0 ]]; then
    echo "- (none found in manifests)"
  else
    for n in "${CLUSTERROLE_NAMES[@]}"; do
      echo "- \`${n}\`"
    done
  fi
  echo ""
  echo "## Cluster-admin required"
  echo ""
  echo "\`${CLUSTER_ADMIN_REQUIRED}\`"
  echo ""
  echo "## runtimeClassName opinion"
  echo ""
  echo "**Opinionated:** ${RUNTIME_OPINIONATED}"
  echo ""
  echo "**Values found:** \`${RUNTIME_VALUES}\`"
  echo ""
  echo "> If the controller hardcodes a runtimeClassName, we must either match that value"
  echo "> in our AKS RuntimeClass manifest or patch the upstream default via Helm values."
  echo ""
  echo "## Recommended RBAC scope"
  echo ""
  echo "\`${RECOMMENDED_RBAC}\`"
  echo ""
  echo "## Raw evidence"
  echo ""
  echo "### apiextensions.k8s.io files"
  echo "\`\`\`"
  echo "${APIEXT_REFS:-none}"
  echo "\`\`\`"
  echo ""
  echo "### rbac.authorization.k8s.io Go source files"
  echo "\`\`\`"
  echo "${RBAC_GO_REFS:-none}"
  echo "\`\`\`"
  echo ""
  echo "### runtimeClassName references (first 40 lines)"
  echo "\`\`\`"
  echo "${RUNTIME_CLASS_REFS:-none}"
  echo "\`\`\`"
  echo ""
  echo "## Next steps"
  echo ""
  echo "1. If **Cluster-admin required = yes**, open a GitHub issue on the upstream repo"
  echo "   requesting a namespace-scoped operator mode, and document the delta in this file."
  echo "2. If **runtimeClassName is hardcoded**, add a Helm value override in"
  echo "   \`infra/helm/opensandbox/values.yaml\` and patch in \`infra/helm/opensandbox/templates/\`."
  echo "3. Review each ClusterRole's rules and verify AKS pod-security standards compatibility."
  echo "4. If CRD scope is **Cluster**, ensure the Bicep service principal has"
  echo "   \`Microsoft.Kubernetes/connectedClusters/write\` and CRD create/update at cluster scope."
} > "${OUT_FILE}"

echo ""
echo "==> Wrote findings to: ${OUT_FILE}"

# Cleanup
rm -rf "${TMPDIR}"
echo "==> Cleaned up temp directory."
echo "==> Phase 0 / Task 0.1 spike complete."
