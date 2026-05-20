#!/usr/bin/env bash
# Create the two Notation signing certificates in Key Vault as a post-deploy step.
#
# Why not Bicep:
#   Microsoft.KeyVault/vaults/certificates ARM creation of a NEW self-signed cert
#   returns BadRequest with an empty message. The supported path is the data-plane
#   `az keyvault certificate create`, which this script invokes.
#
# Plan reference: Phase 1 Task 1.3 (B-C4 fix) — dual-cert Notation rotation. This
# script enforces "deployment cannot complete with fewer than 2 certs" by exiting
# non-zero if either cert is missing or has expired.
#
# Usage:
#   KV_NAME=kv-opensandbox-dev ./scripts/post-deploy/create-notation-certs.sh
#
set -euo pipefail

: "${KV_NAME:?Set KV_NAME to the Key Vault name (e.g., kv-opensandbox-dev)}"

# Default cert policy from `az`, then we override subject + EKU for code signing.
POLICY_FILE=$(mktemp)
trap 'rm -f "$POLICY_FILE"' EXIT

build_policy() {
  local subject="$1"
  cat > "$POLICY_FILE" <<JSON
{
  "issuerParameters": { "name": "Self", "certificateType": null },
  "keyProperties": {
    "exportable": false,
    "keyType": "EC",
    "keySize": 256,
    "curve": "P-256",
    "reuseKey": false
  },
  "secretProperties": { "contentType": "application/x-pem-file" },
  "x509CertificateProperties": {
    "subject": "${subject}",
    "validityInMonths": 12,
    "keyUsage": ["digitalSignature"],
    "ekus": ["1.3.6.1.5.5.7.3.3"]
  },
  "lifetimeActions": [
    {
      "trigger": { "daysBeforeExpiry": 21 },
      "action": { "actionType": "EmailContacts" }
    }
  ]
}
JSON
}

ensure_cert() {
  local name="$1"
  local subject="$2"
  if az keyvault certificate show --vault-name "$KV_NAME" -n "$name" --query "id" -o tsv >/dev/null 2>&1; then
    echo "  ✓ ${name} already exists in ${KV_NAME}"
    return 0
  fi
  echo "  → creating ${name} in ${KV_NAME} ..."
  build_policy "$subject"
  az keyvault certificate create \
    --vault-name "$KV_NAME" \
    --name "$name" \
    --policy "@$POLICY_FILE" \
    >/dev/null
  echo "  ✓ ${name} created"
}

echo "Provisioning Notation signing certs in ${KV_NAME}"
ensure_cert "notation-primary"   "CN=notation-primary-${KV_NAME}"
ensure_cert "notation-secondary" "CN=notation-secondary-${KV_NAME}"

# Final invariant: BOTH certs must exist (Pre-Mortem #2)
for n in notation-primary notation-secondary; do
  if ! az keyvault certificate show --vault-name "$KV_NAME" -n "$n" --query "id" -o tsv >/dev/null 2>&1; then
    echo "FAIL: ${n} missing in ${KV_NAME}; deployment is NOT complete." >&2
    exit 1
  fi
done

echo "Both Notation certs present in ${KV_NAME}. Dual-cert TrustPolicy invariant holds."
