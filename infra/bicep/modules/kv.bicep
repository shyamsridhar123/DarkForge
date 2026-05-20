// modules/kv.bicep — Key Vault with RBAC, private endpoint, purge protection,
//   soft-delete 90 days, TWO Notation signing certificates (notation-primary, notation-secondary).
//
// Plan reference: Phase 1 Task 1.3 (B-C4 fix) — IaC-enforced dual-cert Notation rotation.
// Pre-mortem #2: Ratify TrustPolicy must always reference TWO trustedCerts.
//   cert overlap ≥ 14 days is the OPERATOR / RUNBOOK responsibility:
//   - Mint new secondary at 21 days remaining lifetime of current primary.
//   - Remove old primary only at 7 days remaining lifetime.
//   - IaC here provisions both certs with a self-signed issuer.
//   - Real CA issuance (e.g., DigiCert via AKV Issuer) is an OPERATOR step — not automated here.
//   - A deployment script should assert BOTH certs exist before deployment is marked complete.

targetScope = 'resourceGroup'

param env string
param location string
param privateEndpointSubnetId string
param vnetId string
param lawId string

// ---------------------------------------------------------------------------
// Key Vault
// API version 2023-07-01 stable.
// ---------------------------------------------------------------------------

resource kv 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: 'kv-opensandbox-${env}'
  location: location
  properties: {
    sku: { family: 'A', name: 'premium' }
    tenantId: subscription().tenantId
    enableRbacAuthorization: true   // RBAC mode — no access policies
    enablePurgeProtection: true
    softDeleteRetentionInDays: 90
    enabledForDiskEncryption: false
    enabledForDeployment: false
    enabledForTemplateDeployment: false
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
// Notation signing certificates — created as a POST-DEPLOY step, not in Bicep.
//
// Why not Bicep:
//   Microsoft.KeyVault/vaults/certificates ARM creation of a NEW self-signed cert
//   returns BadRequest with an empty message — the resource provider expects the
//   cert to already exist (it can be referenced/read, not created). The supported
//   way to create a cert is via the data-plane API:
//       az keyvault certificate create --vault-name <kv> -n notation-primary    --policy "$(az keyvault certificate get-default-policy)"
//       az keyvault certificate create --vault-name <kv> -n notation-secondary  --policy "$(az keyvault certificate get-default-policy)"
//   See scripts/post-deploy/create-notation-certs.sh for the canonical command.
//
// Plan reference: Phase 1 Task 1.3 (B-C4 fix) still in force — IaC-enforced dual-cert
//   Notation rotation. The IaC enforcement now lives in the post-deploy script and
//   the deployment-script wrapper in main.bicep (deployScripts) which asserts both
//   certs exist before marking the deployment complete.
// ---------------------------------------------------------------------------
// Private DNS Zone for Key Vault
// ---------------------------------------------------------------------------

resource kvPrivateDnsZone 'Microsoft.Network/privateDnsZones@2020-06-01' = {
  name: 'privatelink.vaultcore.azure.net'
  location: 'global'
}

resource kvDnsVnetLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: kvPrivateDnsZone
  name: 'link-kv-${env}'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: { id: vnetId }
  }
}

// ---------------------------------------------------------------------------
// Private Endpoint
// ---------------------------------------------------------------------------

resource kvPe 'Microsoft.Network/privateEndpoints@2023-11-01' = {
  name: 'pe-kv-opensandbox-${env}'
  location: location
  properties: {
    subnet: { id: privateEndpointSubnetId }
    privateLinkServiceConnections: [
      {
        name: 'pe-kv-conn-${env}'
        properties: {
          privateLinkServiceId: kv.id
          groupIds: ['vault']
        }
      }
    ]
  }
}

resource kvPeDnsGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-11-01' = {
  parent: kvPe
  name: 'kvDnsZoneGroup'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'privatelink-vaultcore-azure-net'
        properties: {
          privateDnsZoneId: kvPrivateDnsZone.id
        }
      }
    ]
  }
}

// ---------------------------------------------------------------------------
// Diagnostic settings → LAW
// ---------------------------------------------------------------------------

resource kvDiag 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'diag-kv-${env}'
  scope: kv
  properties: {
    workspaceId: lawId
    logs: [
      { category: 'AuditEvent', enabled: true }
      { category: 'AzurePolicyEvaluationDetails', enabled: true }
    ]
    metrics: [
      { category: 'AllMetrics', enabled: true }
    ]
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------

output kvId string = kv.id
output kvName string = kv.name
output kvUri string = kv.properties.vaultUri
// notation cert names are constants assumed by the post-deploy script
output notationPrimaryCertName string = 'notation-primary'
output notationSecondaryCertName string = 'notation-secondary'
