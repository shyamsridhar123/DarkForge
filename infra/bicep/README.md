# OpenSandbox-on-Azure — Bicep IaC

Infrastructure-as-Code for the OpenSandbox hybrid ACA + AKS+Kata platform on Azure East US 2.

Plan reference: `ralplan-implement-opensandbox-in-azure.md` (v0.3 FINAL, Architect+Critic APPROVED).

---

## File Layout

```
infra/bicep/
├── main.bicep                        # Subscription-scoped root deployment
├── parameters/
│   ├── dev.parameters.json
│   └── prod.parameters.json
└── modules/
    ├── network.bicep                 # VNet, subnets, NSGs, NAT GW, UDR
    ├── aks.bicep                     # Private AKS, Cilium-ACNS, Kata pool
    ├── acr.bicep                     # ACR Premium + private endpoint
    ├── kv.bicep                      # Key Vault + dual Notation certs
    ├── entra.bicep                   # Custom RBAC role definitions (subscription scope)
    ├── firewall.bicep                # Azure Firewall Standard/Premium (conditional SKU)
    ├── appgw.bicep                   # Application Gateway WAF_v2 + WAF policy
    ├── observability.bicep           # LAW, App Insights, Event Hubs, Stream Analytics
    ├── aca.bicep                     # ACA environment + 3 apps (control-plane, portal-api, portal-frontend)
    └── user.bicep                    # Per-user UAMI + federated cred + KV (dynamic, NOT in main.bicep)
```

---

## Prerequisites

### 1. Entra App Registrations (manual — cannot be done from Bicep)

Run these commands **before** deploying Bicep or immediately after the first `what-if`:

```bash
ENV=dev   # or prod

# OpenSandbox API app (OBO audience + AKS AAD integration)
API_APP_ID=$(az ad app create \
  --display-name "opensandbox-api-${ENV}" \
  --identifier-uris "api://opensandbox-api-${ENV}" \
  --sign-in-audience AzureADMyOrg \
  --query appId -o tsv)

az ad sp create --id "$API_APP_ID"

# OpenSandbox Portal app (ACA Easy Auth)
PORTAL_APP_ID=$(az ad app create \
  --display-name "opensandbox-portal-${ENV}" \
  --sign-in-audience AzureADMyOrg \
  --query appId -o tsv)

az ad sp create --id "$PORTAL_APP_ID"

# Add redirect URI after AppGW FQDN is known (post-deploy step):
# az ad app update --id "$PORTAL_APP_ID" \
#   --web-redirect-uris "https://<appgw-fqdn>/auth/callback"

echo "apiAppId: $API_APP_ID"
echo "portalAppId: $PORTAL_APP_ID"
```

Populate `apiAppId` and `portalAppId` in `parameters/${ENV}.parameters.json`.

### 2. Entra Group for AKS admin

Create an Entra security group for cluster administrators and collect its Object ID:

```bash
GROUP_OID=$(az ad group create \
  --display-name "opensandbox-aks-admins-${ENV}" \
  --mail-nickname "opensandbox-aks-admins-${ENV}" \
  --query id -o tsv)
echo "aksAdminGroupObjectIds: [\"$GROUP_OID\"]"
```

Populate `aksAdminGroupObjectIds` in the parameters file.

---

## Deployment Order

Modules have explicit `dependsOn` chains in `main.bicep`. The effective deploy order is:

1. **Resource Group** (`main.bicep` — subscription scope)
2. **observability** — LAW must exist first; all other modules reference `lawId`
3. **network** — VNet + subnets needed by all compute modules
4. **firewall** — depends on network (AzureFirewallSubnet)
5. **acr** + **kv** — parallel; both depend on network (snet-pe)
6. **entra** — subscription-scoped role definitions; parallel with above
7. **aks** — depends on network subnets + ACR (AcrPull role assignment)
8. **aca** — depends on network (snet-aca) + observability
9. **appgw** — depends on network (snet-appgw) + ACA static IP

### What-if (validate before deploy)

```bash
az deployment sub what-if \
  --location eastus2 \
  --template-file infra/bicep/main.bicep \
  --parameters infra/bicep/parameters/dev.parameters.json
```

### Deploy

```bash
az deployment sub create \
  --location eastus2 \
  --template-file infra/bicep/main.bicep \
  --parameters infra/bicep/parameters/dev.parameters.json \
  --name opensandbox-dev-$(date +%Y%m%d%H%M%S)
```

Expected deploy time: **15–25 minutes** (AKS cluster creation dominates).

---

## Post-Deploy Steps

### AKS credentials

```bash
az aks get-credentials \
  --resource-group rg-opensandbox-dev \
  --name aks-opensandbox-dev \
  --overwrite-existing
```

### Verify node pools

```bash
kubectl get nodes -L agentpool,kubernetes.azure.com/runtime
kubectl get runtimeclass kata-mshv-vm-isolation
```

### Capture AKS server app ID (for OBO audience)

```bash
AKS_SERVER_APP_ID=$(az aks show \
  --resource-group rg-opensandbox-dev \
  --name aks-opensandbox-dev \
  --query aadProfile.serverAppId -o tsv)
echo "aksServerAppId: $AKS_SERVER_APP_ID"
```

Update `parameters/dev.parameters.json` and redeploy to propagate to ACA env vars.

### SSL Certificate for App Gateway

The AppGW listener stub references a placeholder cert. Before production use:

```bash
# Option A: Upload PFX directly
az network application-gateway ssl-cert create \
  --resource-group rg-opensandbox-dev \
  --gateway-name agw-opensandbox-dev \
  --name opensandbox-tls \
  --cert-file /path/to/cert.pfx \
  --cert-password <password>

# Option B: Reference Key Vault certificate (recommended)
# Add KV reference to appgw.bicep sslCertificates array and redeploy.
```

Then uncomment the `sslCertificate` reference in `modules/appgw.bicep` and redeploy.

---

## `egressEnforcementTier` Parameter

This parameter is set based on the result of **Phase 0 Task 0.4** (Cilium ACNS L7 on Kata spike):

| Value | When to use | Firewall behavior |
|-------|-------------|-------------------|
| `standard` | Phase 0 passes — Cilium L7 works on Kata pods | Firewall Standard SKU; network rules as L3/L4 backup. Cilium handles app-layer FQDN enforcement. |
| `premium` | Phase 0 fails — Cilium L7 ineffective on Kata | Firewall Premium SKU; application rules with SNI-based HTTPS filtering (no TLS MITM). Primary L7 egress enforcer. |

Document the Phase 0 decision in `docs/integration-spikes.md` before deploying to prod.

---

## Notation Cert Rotation

Key Vault provisions two certificates (`notation-primary`, `notation-secondary`) with self-signed issuers.
Replace with a trusted CA issuer for production (see `kv.bicep` comments).

**Operator rotation runbook (enforced by runbook, not Bicep alone):**

1. At 21 days remaining on `notation-primary`: mint a new `notation-secondary`.
2. Update Ratify TrustPolicy to trust BOTH certs (overlap ≥ 14 days mandatory).
3. Run the rotation canary CI test — sign a test image with OLD cert and NEW cert; both must schedule.
4. At 7 days remaining: remove `notation-primary` from TrustPolicy; promote secondary to primary.
5. Mint a new secondary for the next rotation cycle.

See plan pre-mortem #2 for full rationale and canary CI test specification.

---

## Per-User Provisioning (`modules/user.bicep`)

`user.bicep` is invoked dynamically by the FastAPI control plane at `POST /users/<oid>/provision`. It is **not** called from `main.bicep`.

```bash
# Called by control plane (example — actual call is from Python/SDK):
az deployment group create \
  --resource-group rg-opensandbox-dev \
  --template-file infra/bicep/modules/user.bicep \
  --parameters \
    env=dev \
    location=eastus2 \
    userOid=<full-oid> \
    shortOid=<first-8-chars-of-oid> \
    aksOidcIssuerUrl=<oidc-issuer-url>
```

The control plane asserts the federated credential is queryable before returning HTTP 200 (synchronous propagation probe — plan pre-mortem #3).

---

## Parameter Customization

| Parameter | Dev default | Prod recommendation |
|-----------|-------------|---------------------|
| `env` | `dev` | `prod` |
| `egressEnforcementTier` | `standard` | Based on Phase 0 spike result |
| `aksAdminGroupObjectIds` | 1 group OID | 2+ group OIDs (platform + on-call) |
| `lawRetentionDays` | `30` | `90` |
| `kubernetesVersion` | `1.31` | Latest stable patch of same minor |

---

## DR Runbook Summary

Quarterly DR drill (plan Task 6.8):

```bash
# 1. ACR cold copy to backup region
az acr import --name acropensandboxprod \
  --source acropensandboxprod.azurecr.io/<image>:<tag> \
  --image <image>:<tag>

# 2. KV backup (GA feature)
az keyvault backup start \
  --hsm-name kv-opensandbox-prod \
  --storage-account-name <backup-sa> \
  --blob-container-name kvbackup \
  --storage-resource-uri https://<backup-sa>.blob.core.windows.net/kvbackup

# 3. LAW archive: configure Diagnostic Settings to archive to storage account (done via observability.bicep)

# 4. KV restore drill
az keyvault restore start \
  --hsm-name kv-opensandbox-prod-restore \
  --storage-account-name <backup-sa> \
  --blob-container-name kvbackup \
  --backup-folder <folder>
```

RTO target: 4 hours. RPO target: 24 hours.
