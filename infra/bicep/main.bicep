// main.bicep — OpenSandbox-on-Azure root deployment
// Plan reference: RALPLAN-DR Summary, Phase 1 (Tasks 1.1-1.6), ADR (FINAL)
// Scope: subscription — creates resource group then delegates to all modules.

targetScope = 'subscription'

// ---------------------------------------------------------------------------
// Parameters
// ---------------------------------------------------------------------------

@description('Environment name (dev | prod).')
@allowed(['dev', 'prod'])
param env string = 'dev'

@description('Azure region for all resources.')
param location string = 'eastus2'

@description('AAD group object IDs that receive cluster-admin (AKS Kubernetes-RBAC).')
param aksAdminGroupObjectIds array

@description('Egress enforcement tier. "standard" = Cilium L7 + Firewall L3/L4 backup. "premium" = Firewall Premium as primary L7 enforcer (Phase 0 fallback).')
@allowed(['standard', 'premium'])
param egressEnforcementTier string = 'standard'

@description('AAD-integrated AKS server application ID (used for OBO audience). Obtain from cluster aadProfile.serverAppID after first deploy or from Entra app registration.')
param aksServerAppId string

@description('Entra app IDs — populated manually after running az ad app create commands in modules/entra.bicep comments.')
param apiAppId string = ''
param portalAppId string = ''

@description('Log Analytics retention days.')
param lawRetentionDays int = 30

// ---------------------------------------------------------------------------
// Resource Group
// ---------------------------------------------------------------------------

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: 'rg-opensandbox-${env}'
  location: location
}

// ---------------------------------------------------------------------------
// Module: Observability (creates LAW first — other modules depend on its ID)
// ---------------------------------------------------------------------------

module observability 'modules/observability.bicep' = {
  name: 'observability'
  scope: rg
  params: {
    env: env
    location: location
    retentionDays: lawRetentionDays
  }
}

// ---------------------------------------------------------------------------
// Module: Network
// ---------------------------------------------------------------------------

module network 'modules/network.bicep' = {
  name: 'network'
  scope: rg
  params: {
    env: env
    location: location
  }
}

// ---------------------------------------------------------------------------
// Module: Firewall
// ---------------------------------------------------------------------------

module firewall 'modules/firewall.bicep' = {
  name: 'firewall'
  scope: rg
  params: {
    env: env
    location: location
    egressEnforcementTier: egressEnforcementTier
    vnetId: network.outputs.vnetId
    firewallSubnetId: network.outputs.firewallSubnetId
    lawId: observability.outputs.lawId
  }
}

// ---------------------------------------------------------------------------
// Module: ACR
// ---------------------------------------------------------------------------

module acr 'modules/acr.bicep' = {
  name: 'acr'
  scope: rg
  params: {
    env: env
    location: location
    privateEndpointSubnetId: network.outputs.peSubnetId
    vnetId: network.outputs.vnetId
    lawId: observability.outputs.lawId
  }
}

// ---------------------------------------------------------------------------
// Module: Key Vault
// ---------------------------------------------------------------------------

module kv 'modules/kv.bicep' = {
  name: 'kv'
  scope: rg
  params: {
    env: env
    location: location
    privateEndpointSubnetId: network.outputs.peSubnetId
    vnetId: network.outputs.vnetId
    lawId: observability.outputs.lawId
  }
}

// ---------------------------------------------------------------------------
// Module: Entra roles (subscription-scoped role definitions)
// ---------------------------------------------------------------------------

module entra 'modules/entra.bicep' = {
  name: 'entra'
  params: {
    apiAppId: apiAppId
    portalAppId: portalAppId
  }
}

// ---------------------------------------------------------------------------
// Module: AKS
// ---------------------------------------------------------------------------

module aks 'modules/aks.bicep' = {
  name: 'aks'
  scope: rg
  params: {
    env: env
    location: location
    aksAdminGroupObjectIds: aksAdminGroupObjectIds
    systemSubnetId: network.outputs.systemSubnetId
    kataSubnetId: network.outputs.kataSubnetId
    lawId: observability.outputs.lawId
    acrId: acr.outputs.acrId
  }
}

// ---------------------------------------------------------------------------
// Module: App Gateway + WAF
// ---------------------------------------------------------------------------

module appgw 'modules/appgw.bicep' = {
  name: 'appgw'
  scope: rg
  params: {
    env: env
    location: location
    appgwSubnetId: network.outputs.appgwSubnetId
    acaEnvStaticIp: aca.outputs.acaEnvStaticIp
    lawId: observability.outputs.lawId
  }
}

// ---------------------------------------------------------------------------
// Module: ACA environment + apps
// ---------------------------------------------------------------------------

module aca 'modules/aca.bicep' = {
  name: 'aca'
  scope: rg
  params: {
    env: env
    location: location
    acaSubnetId: network.outputs.acaSubnetId
    lawId: observability.outputs.lawId
    appInsightsConnectionString: observability.outputs.appInsightsConnectionString
    portalAppId: portalAppId
    aksServerAppId: aksServerAppId
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------

output resourceGroupName string = rg.name
output aksClusterName string = aks.outputs.aksClusterName
output acrLoginServer string = acr.outputs.acrLoginServer
output lawId string = observability.outputs.lawId
output acaEnvStaticIp string = aca.outputs.acaEnvStaticIp
output firewallPrivateIp string = firewall.outputs.firewallPrivateIp
