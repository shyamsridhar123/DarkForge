# Phase 0 Spikes — README

> **Plan tasks:** Phase 0, Tasks 0.1 – 0.4  
> **Purpose:** Validate upstream OpenSandbox assumptions and AKS integration behaviour *before* committing to Phase 1 IaC. Phase 0 gates Phase 1.

---

## Prerequisites

| Tool | Minimum version | Notes |
|------|----------------|-------|
| `az` CLI | 2.58+ | `az login` with subscription access |
| `kubectl` | 1.29+ | Context pre-configured for dev AKS cluster |
| `helm` | 3.14+ | |
| `kind` | 0.22+ | For local spike only (Task 0.3) |
| `git` | 2.40+ | |
| `hubble` CLI | 0.13+ | Optional — for Cilium flow evidence in spike 2 |
| AKS dev cluster | — | Kata Pod Sandboxing preview enabled; Cilium ACNS enabled |
| Azure subscription | — | With AKS Kata preview feature registered |

### Register AKS Kata preview

```bash
az feature register --namespace Microsoft.ContainerService \
  --name KataMshvVmIsolationPreview
az provider register -n Microsoft.ContainerService
```

---

## Spike 1 — `spike-opensandbox-crd.sh` (Task 0.1)

**What it validates:** OpenSandbox CRD scope (Namespaced vs Cluster), ClusterRole requirements, and whether the controller is opinionated about `runtimeClassName`.

**Expected runtime:** 2–5 minutes (git clone + grep).

**No live cluster required.**

### Run

```bash
bash scripts/phase0/spike-opensandbox-crd.sh
```

### Output

`docs/upstream-delta.md` — structured summary:

- **CRDs:** name, apiVersion, scope
- **ClusterRoles required:** list
- **Cluster-admin required:** yes / no / uncertain
- **Recommended RBAC scope:** namespace | cluster
- **runtimeClassName opinion:** hardcoded value or delegate

### Interpreting results

| Finding | Action |
|---------|--------|
| Cluster-admin required = **yes** | Open upstream issue; document delta; plan patched Helm values |
| runtimeClassName hardcoded | Add Helm value override in `infra/helm/opensandbox/values.yaml` |
| CRD scope = Cluster | Ensure Bicep SP has CRD create/update at cluster scope |
| No CRDs found | Check Helm chart release artefacts — CRDs may ship separately |

### On failure

If `git clone` fails (network/auth): manually clone the repo and set `CLONE_DIR` env var to point to the local checkout before running.

---

## Spike 2 — `spike-cilium-kata-l7.sh` (Task 0.4)

**What it validates:** Whether Cilium ACNS L7 FQDN `NetworkPolicy` is enforced inside Kata pods. This determines the `egressEnforcementTier` Bicep parameter (and whether Azure Firewall Standard or Premium is deployed).

**Expected runtime:** 5–10 minutes.

**Requires:** Live dev AKS cluster with Kata + Cilium ACNS.

### Run

```bash
# Ensure kubectl context points to dev AKS
az aks get-credentials -g rg-opensandbox-dev -n aks-opensandbox-dev
bash scripts/phase0/spike-cilium-kata-l7.sh
```

### Output

`docs/integration-spikes.md` — verdict + evidence:

```
Cilium L7 on Kata: PASS | FAIL
Evidence: <Hubble flow IDs or error messages>
Recommended egressEnforcementTier: standard | premium
```

### Interpreting results

| Result | Meaning | Action |
|--------|---------|--------|
| **PASS** | L7 FQDN policy enforced inside Kata | Set `egressEnforcementTier = 'standard'` in Bicep params |
| **FAIL** | L7 not enforced | Set `egressEnforcementTier = 'premium'`; Azure Firewall Premium with SNI inspection becomes primary |
| **PARTIAL-FAIL** | Allow blocked too | Check Cilium ACNS version; DNS inside Kata VM may not resolve through Cilium proxy |

### On failure

1. Check Cilium agent version: `kubectl -n kube-system get ds cilium -o jsonpath='{.spec.template.spec.containers[0].image}'`
2. Verify ACNS is enabled: `az aks show -g <rg> -n <cluster> --query networkProfile.networkPolicy`
3. Check Hubble relay is running: `kubectl -n kube-system get pod -l k8s-app=hubble-relay`
4. If Kata pods can't reach DNS, check `CiliumNetworkPolicy` allows UDP/53 to kube-dns.

---

## Spike 3 — `spike-kind-local.sh` (Task 0.3)

**What it validates:** OpenSandbox controller Helm installation succeeds; controller accepts a pod spec with `runtimeClassName: kata-vm-isolation` (pod will remain Pending — no Kata runtime in kind, but that is expected).

**Expected runtime:** 5–10 minutes. Time-boxed at 15 minutes.

**No AKS required.**

### Run

```bash
bash scripts/phase0/spike-kind-local.sh
```

### Output

Console output showing:
- Helm install result
- Pod phase (`Pending` = controller accepted spec — **expected success**)
- Controller logs

### Interpreting results

| Pod phase | Meaning |
|-----------|---------|
| `Pending` | ✅ Controller accepted the spec — runtimeClass is registered but handler missing (expected in kind) |
| `Failed` | ❌ Controller rejected spec — check controller logs for validation errors |
| `Running` | ⚠️ Unexpected in kind; verify runtimeClass handler is not accidentally present |

### On failure

- If Helm chart not found at upstream URL: copy chart from `infra/helm/opensandbox/` (must be committed first).
- If `kind create cluster` fails: ensure Docker daemon is running; check `kind version`.

---

## Interpreting combined Phase 0 results

| Task 0.1 CRD scope | Task 0.4 L7 result | Task 0.3 controller | Proceed? |
|--------------------|-------------------|---------------------|----------|
| Namespace or no cluster-admin | PASS | Accepted spec | ✅ Proceed to Phase 1 with `standard` tier |
| Namespace or no cluster-admin | FAIL | Accepted spec | ✅ Proceed to Phase 1 with `premium` tier |
| cluster-admin required | Any | Any | ⚠️ Document delta; patch Helm; then proceed |
| Any | Any | Rejected spec | ❌ Investigate controller validation; may need fork patch |

### Escalation

If **any spike produces an un-mitigatable blocker**, escalate to the architecture decision: activate **Option A fallback** (everything-on-AKS without ACA split) as documented in the ADR.  Record the decision in `docs/integration-spikes.md` before starting Phase 1.
