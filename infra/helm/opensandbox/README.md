# OpenSandbox Helm Chart

Helm chart for the OpenSandbox runtime on AKS+Kata. Implements the consensus plan RALPLAN-DR v0.3 FINAL.

## Architecture summary

| Component | Node pool | Mechanism |
|-----------|-----------|-----------|
| `controller` | system | Deployment — OpenSandbox K8s controller with Workload Identity |
| `execd` | kata | DaemonSet — execution daemon (runc, manages Kata pods) |
| `image-prewarm` | kata | DaemonSet — crictl pre-pulls curated images; removes node taint when done |
| Cilium policies | cluster | CiliumClusterwideNetworkPolicy (default-deny) + per-namespace CiliumNetworkPolicy (FQDN allowlist) |
| Ratify | opensandbox-system | Sub-chart — Notation image signature admission |
| Gatekeeper constraints | cluster | OPA constraints (signed images, no hostPath, no privileged, allowed registries, non-root) |

---

## Prerequisites

Before running `helm install`, ensure:

1. **AKS cluster** with:
   - Kata Pod Sandboxing enabled (`kata-vm-isolation` RuntimeClass present)
   - Cilium ACNS installed (provides `CiliumNetworkPolicy` / `CiliumClusterwideNetworkPolicy` CRDs)
   - Azure AD integration (`--aad-admin-group-object-ids` set per plan NICE-1)
   - Workload Identity enabled (`--enable-workload-identity`)
   - Two node pools: `system` (e.g. Standard_D4s_v5) and `kata` (e.g. Standard_D8s_v5 Gen2)

2. **Gatekeeper** installed on the cluster with the ConstraintTemplate CRDs for:
   - `K8sRequiredImagesSigned`
   - `K8sBlockHostPath`
   - `K8sNoPrivilegedContainers`
   - `K8sAllowedRegistries`
   - `K8sRequiredRunAsNonRoot`
   (These are typically installed via the Azure Policy add-on for AKS.)

3. **Ratify** CRDs: the `ratify` sub-chart installs them, but pre-installing is also fine.

4. **csi-secrets-store** with Azure Key Vault provider installed, and two Notation cert secrets pre-created:
   - `ratify-notation-cert-primary`
   - `ratify-notation-cert-secondary`

5. **UAMI** (`id-opensandbox-controller`) with Federated Identity Credential for the `opensandbox-controller` ServiceAccount.

6. **Phase 0 tasks 0.1–0.4 complete** (upstream CRD scope confirmed, Cilium L7 on Kata validated).

---

## Install

```bash
# Add Cilium repo (for CRDs, if not pre-installed by AKS ACNS)
helm repo add cilium https://helm.cilium.io/

# Update chart dependencies
helm dependency update infra/helm/opensandbox/

# Install (dev)
helm install opensandbox infra/helm/opensandbox/ \
  -f infra/helm/opensandbox/values.yaml \
  -f infra/helm/opensandbox/values.dev.yaml \
  --namespace opensandbox-system \
  --create-namespace \
  --set controller.uamiClientId=<uami-client-id-from-bicep> \
  --set ratify.trustPolicy.registryScope=<acr-hostname> \
  --set azurePolicy.allowedRegistry=<acr-hostname> \
  --set cilium.firewallPrivateIp=<firewall-private-ip-from-bicep>

# Install (production)
helm install opensandbox infra/helm/opensandbox/ \
  -f infra/helm/opensandbox/values.yaml \
  --namespace opensandbox-system \
  --create-namespace \
  --set controller.image.repository=<acr>.azurecr.io/opensandbox/controller \
  --set controller.image.tag=<version> \
  --set execd.image.repository=<acr>.azurecr.io/opensandbox/execd \
  --set execd.image.tag=<version> \
  --set controller.uamiClientId=<uami-client-id> \
  --set ratify.trustPolicy.registryScope=<acr>.azurecr.io \
  --set azurePolicy.allowedRegistry=<acr>.azurecr.io \
  --set cilium.firewallPrivateIp=<firewall-ip>
```

---

## Upgrade

```bash
helm upgrade opensandbox infra/helm/opensandbox/ \
  -f infra/helm/opensandbox/values.yaml \
  --reuse-values \
  --set controller.image.tag=<new-version>
```

Run `helm diff upgrade` (requires the `helm-diff` plugin) before upgrading in production.

---

## Values reference

| Key | Default | Description |
|-----|---------|-------------|
| `controller.image.repository` | `""` | Controller image repo. **Must be set.** |
| `controller.image.tag` | `""` | Controller image tag. **Must be set.** |
| `controller.image.pullPolicy` | `IfNotPresent` | Image pull policy. |
| `controller.uamiClientId` | `""` | UAMI client ID for Workload Identity. **Must be set.** |
| `controller.replicaCount` | `2` | Controller replica count. |
| `controller.nodeSelector` | `{kubernetes.azure.com/agentpool: system}` | System pool only. Do not change. |
| `controller.tolerations` | `[]` | Deliberately empty — must NOT tolerate `runtime=kata`. |
| `controller.rbac.scope` | `namespace` | RBAC scope. Must be `namespace` unless Phase 0 task 0.1 requires `cluster`. |
| `controller.resources` | `100m/500m CPU, 128Mi/512Mi Mem` | Resource requests/limits. |
| `execd.image.repository` | `""` | execd image repo. **Must be set.** |
| `execd.image.tag` | `""` | execd image tag. **Must be set.** |
| `execd.tolerations` | `[runtime=kata:NoSchedule]` | Kata node taint toleration. |
| `execd.hostPathMounts` | `[]` | hostPath mounts (empty = Azure Policy compliant). |
| `imagePreWarm.enabled` | `true` | Enable image pre-warm DaemonSet. |
| `imagePreWarm.images` | `[]` | List of image refs to pre-pull. **Must be set for production.** |
| `imagePreWarm.parallelPullsPerNode` | `2` | Max concurrent crictl pulls per node. |
| `imagePreWarm.parallelPullsClusterWide` | `30` | Soft cluster-wide pull concurrency guidance. |
| `ratify.trustPolicy.primaryCertSecretName` | `ratify-notation-cert-primary` | Primary Notation cert K8s Secret name. |
| `ratify.trustPolicy.secondaryCertSecretName` | `ratify-notation-cert-secondary` | Secondary Notation cert K8s Secret name. |
| `ratify.trustPolicy.registryScope` | `""` | ACR hostname scope for Ratify trust policy. |
| `cilium.defaultDeny` | `true` | Enable CiliumClusterwideNetworkPolicy default-deny. |
| `cilium.fqdnAllowlist` | `[pypi.org, ...]` | Egress FQDN allowlist for sandbox pods. |
| `cilium.firewallPrivateIp` | `""` | Azure Firewall private IP. **Must be set for network policy to work.** |
| `azurePolicy.allowedRegistry` | `""` | ACR hostname for K8sAllowedRegistries constraint. |

---

## Dependency CRDs

The following CRDs must exist on the cluster **before** `helm install`:

| CRD | Provided by |
|-----|-------------|
| `CiliumNetworkPolicy`, `CiliumClusterwideNetworkPolicy` | AKS ACNS (Cilium) |
| `K8sRequiredImagesSigned`, `K8sBlockHostPath`, etc. | Azure Policy add-on (Gatekeeper) |
| Ratify `Policy`, `Store`, `Verifier` | ratify sub-chart (installed automatically) |

---

## Image pre-warm and node scheduling

The pre-warm mechanism ensures sandbox pods never land on a cold node:

```
Node added by autoscaler
  └─> Node tainted: pre-warm=pending:NoSchedule
      └─> image-prewarm DaemonSet schedules (tolerates pre-warm=pending)
          └─> initContainer: crictl pull all curated images
              └─> initContainer: kubectl patch node → remove pre-warm=pending taint
                  └─> Sandbox pods can now schedule (they tolerate runtime=kata, NOT pre-warm=pending)
```

**Key constraint:** Sandbox pods must have:
```yaml
tolerations:
  - key: runtime
    value: kata
    effect: NoSchedule
# Do NOT add pre-warm=pending toleration to sandbox pods.
```

---

## Cert rotation (Ratify / Notation)

Two certs are maintained at all times. The overlap period is **minimum 14 days** (IaC-enforced via Key Vault cert validity windows in Bicep).

Rotation procedure:
1. Mint a new cert in Key Vault as the new secondary.
2. Update `ratify.trustPolicy.secondaryCertSecretName` in values.
3. `helm upgrade` to pick up the new secret name.
4. Run canary CI: sign test image with new cert AND old cert; assert both pods start.
5. After 7 consecutive canary-passing days, retire the old cert.
6. Update `ratify.trustPolicy.primaryCertSecretName` to the new cert.
7. `helm upgrade` again.

See `runbooks/cert-rotation.md`.

---

## Troubleshooting

### Image pull failures on Kata nodes

```bash
# Check pre-warm DaemonSet logs
kubectl logs -n opensandbox-system -l app.kubernetes.io/component=image-prewarm -c image-puller

# Check if pre-warm=pending taint is still on node
kubectl get nodes -l kubernetes.azure.com/agentpool=kata -o custom-columns='NAME:.metadata.name,TAINTS:.spec.taints'

# Manually remove taint if DaemonSet failed
kubectl taint node <node-name> pre-warm=pending:NoSchedule-
```

### Ratify admission blocking pods

```bash
# Check Ratify logs
kubectl logs -n opensandbox-system -l app.kubernetes.io/name=ratify

# Check if both cert secrets exist
kubectl get secret -n opensandbox-system ratify-notation-cert-primary ratify-notation-cert-secondary

# Verify Notation signature on an image
notation verify <image-ref>
```

### Cilium NetworkPolicy not enforced

```bash
# Check Cilium status on a Kata node
kubectl exec -n kube-system -l k8s-app=cilium -- cilium status

# If Phase 0 task 0.4 showed Cilium L7 is ineffective on Kata pods,
# egress enforcement falls back to Azure Firewall SNI-based filtering.
# Verify Azure Firewall is correctly configured (Task 1.5).
```

### Controller fails to start (missing uamiClientId)

The chart requires `controller.uamiClientId` to be set. If not set:
```
Error: execution error at (opensandbox/templates/controller-deployment.yaml): controller.uamiClientId must be set
```
Provide the value via `--set controller.uamiClientId=<client-id>`.

---

## Phase 0 gate

**Do not proceed to production deployment without completing Phase 0 tasks:**
- `0.1` — OpenSandbox upstream CRD scope confirmed (namespace vs cluster)
- `0.2` — OpenSandbox upstream image ref confirmed
- `0.3` — execd hostPath requirements confirmed
- `0.4` — Cilium ACNS L7 behavior on Kata pods validated

Results documented in `docs/integration-spikes.md`.
