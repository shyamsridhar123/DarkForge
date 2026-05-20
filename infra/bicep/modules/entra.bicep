// modules/entra.bicep — Custom Azure RBAC role definitions at subscription scope.
// Plan reference: Phase 1 Task 1.4 — Entra + UAMI baseline, custom roles.
//
// IMPORTANT — Entra App Registrations cannot be created from Bicep (no AzureAD provider).
// After running this Bicep, execute the following az CLI commands MANUALLY
// (or add to your bootstrap script):
//
//   # 1. OpenSandbox API app (used for OBO audience and AKS AAD integration)
//   az ad app create \
//     --display-name "opensandbox-api-${ENV}" \
//     --identifier-uris "api://opensandbox-api-${ENV}" \
//     --sign-in-audience AzureADMyOrg
//   # Capture appId → set as apiAppId parameter in parameters/${env}.parameters.json
//
//   # 2. OpenSandbox Portal app (ACA Easy Auth)
//   az ad app create \
//     --display-name "opensandbox-portal-${ENV}" \
//     --sign-in-audience AzureADMyOrg
//   # Add redirect URI: https://<appgw-fqdn>/auth/callback
//   # Capture appId → set as portalAppId parameter in parameters/${env}.parameters.json
//
//   # 3. Create service principals for both apps
//   az ad sp create --id <apiAppId>
//   az ad sp create --id <portalAppId>
//
//   # 4. Add API permission: portal app requests access to api app
//   az ad app permission add \
//     --id <portalAppId> \
//     --api <apiAppId> \
//     --api-permissions <scopeId>=Scope
//   az ad app permission grant --id <portalAppId> --api <apiAppId>

targetScope = 'subscription'

param apiAppId string = ''
param portalAppId string = ''

// ---------------------------------------------------------------------------
// Custom Role Definitions
// GUIDs are deterministic per subscription to allow idempotent re-deploys.
// ---------------------------------------------------------------------------

// SandboxCenter Admin — full management of all sandbox projects within this subscription
resource roleSandboxCenterAdmin 'Microsoft.Authorization/roleDefinitions@2022-04-01' = {
  name: guid(subscription().id, 'SandboxCenter Admin')
  properties: {
    roleName: 'SandboxCenter Admin'
    description: 'Full management of the OpenSandbox platform: create/delete projects, manage quotas, view audit logs, manage signing certs.'
    type: 'CustomRole'
    assignableScopes: [subscription().id]
    permissions: [
      {
        actions: [
          'Microsoft.ContainerService/managedClusters/read'
          'Microsoft.ContainerService/managedClusters/listClusterUserCredential/action'
          'Microsoft.ContainerRegistry/registries/read'
          'Microsoft.ContainerRegistry/registries/pull/read'
          'Microsoft.KeyVault/vaults/read'
          'Microsoft.KeyVault/vaults/secrets/read'
          'Microsoft.ManagedIdentity/userAssignedIdentities/*'
          'Microsoft.Authorization/roleAssignments/*'
          'Microsoft.Resources/subscriptions/resourceGroups/read'
          'Microsoft.OperationalInsights/workspaces/read'
          'Microsoft.OperationalInsights/workspaces/query/read'
          'Microsoft.Insights/components/read'
        ]
        notActions: []
        dataActions: [
          'Microsoft.KeyVault/vaults/secrets/getSecret/action'
          'Microsoft.KeyVault/vaults/certificates/read'
        ]
        notDataActions: []
      }
    ]
  }
}

// SandboxProject Admin — manage a specific sandbox project (namespace scope)
resource roleSandboxProjectAdmin 'Microsoft.Authorization/roleDefinitions@2022-04-01' = {
  name: guid(subscription().id, 'SandboxProject Admin')
  properties: {
    roleName: 'SandboxProject Admin'
    description: 'Manage sessions, quotas, and users within a specific sandbox project. Cannot manage platform-level resources.'
    type: 'CustomRole'
    assignableScopes: [subscription().id]
    permissions: [
      {
        actions: [
          'Microsoft.ContainerService/managedClusters/read'
          'Microsoft.ContainerRegistry/registries/read'
          'Microsoft.ContainerRegistry/registries/pull/read'
          'Microsoft.ManagedIdentity/userAssignedIdentities/read'
          'Microsoft.Resources/subscriptions/resourceGroups/read'
          'Microsoft.OperationalInsights/workspaces/read'
          'Microsoft.OperationalInsights/workspaces/query/read'
        ]
        notActions: []
        dataActions: []
        notDataActions: []
      }
    ]
  }
}

// Sandbox User — create and access their own sandbox sessions
resource roleSandboxUser 'Microsoft.Authorization/roleDefinitions@2022-04-01' = {
  name: guid(subscription().id, 'Sandbox User')
  properties: {
    roleName: 'Sandbox User'
    description: 'Create and access own sandbox sessions (default isolated tier). Cannot view other users\' sessions.'
    type: 'CustomRole'
    assignableScopes: [subscription().id]
    permissions: [
      {
        actions: [
          'Microsoft.Resources/subscriptions/resourceGroups/read'
        ]
        notActions: []
        dataActions: [
          'Microsoft.KeyVault/vaults/secrets/getSecret/action'
        ]
        notDataActions: []
      }
    ]
  }
}

// Sandbox User (Low Latency) — opt-in shared-pool tier (audited, rate-limited per Task 3.4)
// This role grants access to the shared warm-pool. Assignment is gated by the control plane.
resource roleSandboxUserLowLatency 'Microsoft.Authorization/roleDefinitions@2022-04-01' = {
  name: guid(subscription().id, 'Sandbox User (Low Latency)')
  properties: {
    roleName: 'Sandbox User (Low Latency)'
    description: 'Opt-in shared-pool (low latency) tier. Access is audited, rate-limited (100 concurrent platform-wide / 5 per user), and token lifetime is 5 minutes. Assignment managed by control plane only.'
    type: 'CustomRole'
    assignableScopes: [subscription().id]
    permissions: [
      {
        actions: [
          'Microsoft.Resources/subscriptions/resourceGroups/read'
        ]
        notActions: []
        dataActions: [
          'Microsoft.KeyVault/vaults/secrets/getSecret/action'
        ]
        notDataActions: []
      }
    ]
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------

output sandboxCenterAdminRoleId string = roleSandboxCenterAdmin.id
output sandboxProjectAdminRoleId string = roleSandboxProjectAdmin.id
output sandboxUserRoleId string = roleSandboxUser.id
output sandboxUserLowLatencyRoleId string = roleSandboxUserLowLatency.id
// App IDs are operator-supplied (cannot be provisioned from Bicep)
output apiAppIdNote string = empty(apiAppId) ? 'NOT SET — run az ad app create and update parameters' : apiAppId
output portalAppIdNote string = empty(portalAppId) ? 'NOT SET — run az ad app create and update parameters' : portalAppId
