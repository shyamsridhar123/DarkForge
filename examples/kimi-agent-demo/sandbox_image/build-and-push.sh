#!/usr/bin/env bash
# build-and-push.sh — Cloud-build the sandbox image using az acr build.
# Does NOT require local Docker — the build runs entirely in ACR Tasks.

set -euo pipefail

ACR_NAME="acropensandboxdemo7075"
IMAGE_TAG="sandbox/base/python:3.12"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEMO_DIR="$(dirname "${SCRIPT_DIR}")"

echo "=== OpenSandbox: Building sandbox image via Azure Container Registry Tasks ==="
echo "  ACR:   ${ACR_NAME}.azurecr.io"
echo "  Image: ${IMAGE_TAG}"
echo "  Context: ${SCRIPT_DIR}"
echo ""

# az acr build is a pure cloud build — no local Docker daemon needed.
# Authentication is handled automatically by the az CLI session.
# (az acr login would invoke Docker; we skip it entirely here.)

echo "[1/1] Submitting cloud build (az acr build) ..."
az acr build \
  --registry "${ACR_NAME}" \
  --image "${IMAGE_TAG}" \
  --file "${SCRIPT_DIR}/Dockerfile" \
  "${SCRIPT_DIR}"

echo ""
echo "=== Build complete ==="

# Report the digest of the pushed image
echo "Image digest:"
az acr repository show \
  --name "${ACR_NAME}" \
  --image "${IMAGE_TAG}" \
  --query "digest" \
  --output tsv
