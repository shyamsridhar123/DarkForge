// modules/user.bicep — Per-user resource provisioning
// Plan reference: Phase 3 Task 3.1-3.4; AC #11 (S-C10 fix) — per-user KV kv-user-<short-oid>.
//
// IMPORTANT: This module is NOT called from main.bicep.
// It is invoked DYNAMICALLY by the FastAPI control-plane at
//   POST /users/<oid>/provision
// once per user onboarding. The control plane calls:
//   az deployment group create \
//     --resource-group rg-opensandbox-${env} \
//     --template-file modules/user.bicep \
//     --parameters env=${env} location=${location} userOid=${oid} shortOid=${shortOid} aksOidcIssuerUrl=${issuerUrl}
//
// Concurrency: the provisioning endpoint uses a distributed lock (Redis or KV lease)
// to prevent duplicate deployments for the same OID. A synchronous propagation probe
// asserts the federated credential is queryable before returning 200 (pre-mortem #3).

targetScope = 'resourceGroup'

param env string
param location string

@description('Full Entra user OID.')
param userOid string

@description('Shortened OID (first 8 chars) used in resource names.')
@maxLength(8)
param shortOid string

@description('AKS OIDC issuer URL from aks.bicep output.')
param aksOidcIssuerUrl string

@description('Kubernetes namespace for this user (e.g. sandbox-<shortOid>).')
param userNamespace string = 'sandbox-${shortOid}'

@description('Kubernetes service account name for this user.')
param userSaName string = 'sa-${shortOid}'

// ---------------------------------------------------------------------------
// User-Assigned Managed Identity
// ---------------------------------------------------------------------------

resource userUami 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-user-${shortOid}'
  location: location
  tags: {
    userOid: userOid
    env: env
  }
}

// ---------------------------------------------------------------------------
// Federated Identity Credential — bound to the user's namespace + SA
// This enables Workload Identity: the pod SA can exchange a K8s token for an
// Azure access token scoped to this UAMI.
// The control plane asserts the FC is queryable before returning 200 (synchronous probe).
// ---------------------------------------------------------------------------

resource userFederatedCred 'Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials@2023-01-31' = {
  parent: userUami
  name: 'fc-${shortOid}'
  properties: {
    issuer: aksOidcIssuerUrl
    subject: 'system:serviceaccount:${userNamespace}:${userSaName}'
    audiences: ['api://AzureADTokenExchange']
  }
}

// ---------------------------------------------------------------------------
// Per-user Key Vault
// AC #11: user's UAMI has Key Vault Secrets User role at this vault's scope ONLY.
// Cross-user secret access returns 403.
// ---------------------------------------------------------------------------

resource userKv 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: 'kv-user-${shortOid}'
  location: location
  tags: {
    userOid: userOid
    env: env
  }
  properties: {
    sku: { family: 'A', name: 'standard' }
    tenantId: subscription().tenantId
    enableRbacAuthorization: true
    enablePurgeProtection: false   // Per-user KVs are ephemeral; purge protection not required
    softDeleteRetentionInDays: 7
    publicNetworkAccess: 'Disabled'
    networkAcls: {
      bypass: 'AzureServices'
      defaultAction: 'Deny'
      ipRules: []
      virtualNetworkRules: []
    }
  }
}

// ---------------------------------------------------------------------------
// RBAC: user's UAMI → Key Vault Secrets User on the per-user KV
// Scoped to userKv only — cannot access other vaults.
// ---------------------------------------------------------------------------

var kvSecretsUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6'

resource userKvRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(userKv.id, userUami.id, kvSecretsUserRoleId)
  scope: userKv
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', kvSecretsUserRoleId)
    principalId: userUami.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// ---------------------------------------------------------------------------
// Outputs — returned to control plane for K8s provisioning step
// ---------------------------------------------------------------------------

output userUamiId string = userUami.id
output userUamiClientId string = userUami.properties.clientId
output userUamiPrincipalId string = userUami.properties.principalId
output userKvId string = userKv.id
output userKvName string = userKv.name
output userKvUri string = userKv.properties.vaultUri
output federatedCredName string = userFederatedCred.name
