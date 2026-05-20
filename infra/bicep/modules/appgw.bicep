// modules/appgw.bicep — Application Gateway v2, WAF_v2 SKU, OWASP CRS 3.2 Prevention mode
// Plan reference: Phase 1 Task 1.1 — App Gateway WAF on snet-appgw.
//   External SDK + portal traffic terminates here; backend = ACA env static IP.

targetScope = 'resourceGroup'

param env string
param location string
param appgwSubnetId string

@description('Static IP of the ACA environment internal load balancer. Obtained from aca.bicep output.')
param acaEnvStaticIp string

param lawId string

// ---------------------------------------------------------------------------
// Public IP for Application Gateway
// ---------------------------------------------------------------------------

resource appgwPip 'Microsoft.Network/publicIPAddresses@2023-11-01' = {
  name: 'pip-appgw-opensandbox-${env}'
  location: location
  sku: { name: 'Standard' }
  zones: ['1', '2', '3']
  properties: {
    publicIPAllocationMethod: 'Static'
    publicIPAddressVersion: 'IPv4'
    dnsSettings: {
      domainNameLabel: 'appgw-opensandbox-${env}'
    }
  }
}

// ---------------------------------------------------------------------------
// WAF Policy — Prevention mode, OWASP CRS 3.2
// ---------------------------------------------------------------------------

resource wafPolicy 'Microsoft.Network/ApplicationGatewayWebApplicationFirewallPolicies@2023-11-01' = {
  name: 'wafpol-opensandbox-${env}'
  location: location
  properties: {
    policySettings: {
      state: 'Enabled'
      mode: 'Prevention'
      requestBodyCheck: true
      maxRequestBodySizeInKb: 128
      fileUploadLimitInMb: 100
    }
    managedRules: {
      managedRuleSets: [
        {
          ruleSetType: 'OWASP'
          ruleSetVersion: '3.2'
        }
        {
          ruleSetType: 'Microsoft_BotManagerRuleSet'
          ruleSetVersion: '1.0'
        }
      ]
      exclusions: []
    }
  }
}

// ---------------------------------------------------------------------------
// Application Gateway v2 — WAF_v2 SKU
// API version 2023-11-01 stable.
// ---------------------------------------------------------------------------

resource appgw 'Microsoft.Network/applicationGateways@2023-11-01' = {
  name: 'agw-opensandbox-${env}'
  location: location
  zones: ['1', '2', '3']
  properties: {
    sku: {
      name: 'WAF_v2'
      tier: 'WAF_v2'
    }
    autoscaleConfiguration: {
      minCapacity: 1
      maxCapacity: 10
    }
    firewallPolicy: { id: wafPolicy.id }
    gatewayIPConfigurations: [
      {
        name: 'appgw-ip-config'
        properties: {
          subnet: { id: appgwSubnetId }
        }
      }
    ]
    frontendIPConfigurations: [
      {
        name: 'appgw-frontend-public'
        properties: {
          publicIPAddress: { id: appgwPip.id }
        }
      }
    ]
    frontendPorts: [
      {
        name: 'port-443'
        properties: { port: 443 }
      }
      {
        name: 'port-80'
        properties: { port: 80 }
      }
    ]
    // SSL certificate: operator must upload or reference KV-backed cert
    // Placeholder self-signed cert must be replaced before production use.
    sslCertificates: []
    // Backend pool — ACA environment internal IP (snet-aca)
    backendAddressPools: [
      {
        name: 'be-aca-env'
        properties: {
          backendAddresses: [
            {
              ipAddress: acaEnvStaticIp
            }
          ]
        }
      }
    ]
    backendHttpSettingsCollection: [
      {
        name: 'be-https-settings'
        properties: {
          port: 443
          protocol: 'Https'
          cookieBasedAffinity: 'Disabled'
          requestTimeout: 120
          pickHostNameFromBackendAddress: true
        }
      }
    ]
    httpListeners: [
      {
        name: 'listener-https'
        properties: {
          frontendIPConfiguration: {
            id: resourceId('Microsoft.Network/applicationGateways/frontendIPConfigurations', 'agw-opensandbox-${env}', 'appgw-frontend-public')
          }
          frontendPort: {
            id: resourceId('Microsoft.Network/applicationGateways/frontendPorts', 'agw-opensandbox-${env}', 'port-443')
          }
          protocol: 'Https'
          firewallPolicy: { id: wafPolicy.id }
          // SSL cert reference: replace 'placeholder' with actual cert name after upload
          // sslCertificate: { id: resourceId('Microsoft.Network/applicationGateways/sslCertificates', 'agw-opensandbox-${env}', 'opensandbox-tls') }
        }
      }
      {
        // HTTP listener for redirect to HTTPS
        name: 'listener-http'
        properties: {
          frontendIPConfiguration: {
            id: resourceId('Microsoft.Network/applicationGateways/frontendIPConfigurations', 'agw-opensandbox-${env}', 'appgw-frontend-public')
          }
          frontendPort: {
            id: resourceId('Microsoft.Network/applicationGateways/frontendPorts', 'agw-opensandbox-${env}', 'port-80')
          }
          protocol: 'Http'
        }
      }
    ]
    redirectConfigurations: [
      {
        name: 'redirect-http-to-https'
        properties: {
          redirectType: 'Permanent'
          targetListener: {
            id: resourceId('Microsoft.Network/applicationGateways/httpListeners', 'agw-opensandbox-${env}', 'listener-https')
          }
          includePath: true
          includeQueryString: true
        }
      }
    ]
    requestRoutingRules: [
      {
        name: 'rule-https-to-aca'
        properties: {
          ruleType: 'Basic'
          priority: 100
          httpListener: {
            id: resourceId('Microsoft.Network/applicationGateways/httpListeners', 'agw-opensandbox-${env}', 'listener-https')
          }
          backendAddressPool: {
            id: resourceId('Microsoft.Network/applicationGateways/backendAddressPools', 'agw-opensandbox-${env}', 'be-aca-env')
          }
          backendHttpSettings: {
            id: resourceId('Microsoft.Network/applicationGateways/backendHttpSettingsCollection', 'agw-opensandbox-${env}', 'be-https-settings')
          }
        }
      }
      {
        name: 'rule-http-redirect'
        properties: {
          ruleType: 'Basic'
          priority: 200
          httpListener: {
            id: resourceId('Microsoft.Network/applicationGateways/httpListeners', 'agw-opensandbox-${env}', 'listener-http')
          }
          redirectConfiguration: {
            id: resourceId('Microsoft.Network/applicationGateways/redirectConfigurations', 'agw-opensandbox-${env}', 'redirect-http-to-https')
          }
        }
      }
    ]
    enableHttp2: true
  }
}

// ---------------------------------------------------------------------------
// Diagnostic settings → LAW
// ---------------------------------------------------------------------------

resource appgwDiag 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'diag-appgw-${env}'
  scope: appgw
  properties: {
    workspaceId: lawId
    logs: [
      { category: 'ApplicationGatewayAccessLog', enabled: true }
      { category: 'ApplicationGatewayFirewallLog', enabled: true }
      { category: 'ApplicationGatewayPerformanceLog', enabled: true }
    ]
    metrics: [
      { category: 'AllMetrics', enabled: true }
    ]
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------

output appgwId string = appgw.id
output appgwName string = appgw.name
output appgwPublicIp string = appgwPip.properties.ipAddress
output appgwFqdn string = appgwPip.properties.dnsSettings.fqdn
