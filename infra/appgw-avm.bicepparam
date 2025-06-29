using './appgw-avm.bicep'

// ============================================================================
// BASIC CONFIGURATION
// ============================================================================

param applicationGatewayName = 'agw-rtaudioagent3-dev'
param location = 'eastus2'

// ============================================================================
// SKU AND SCALING CONFIGURATION
// ============================================================================

param skuName = 'WAF_v2'
param capacity = 2
param enableAutoscaling = true
param autoscaleMinCapacity = 2
param autoscaleMaxCapacity = 10

// ============================================================================
// NETWORKING CONFIGURATION
// ============================================================================

// Replace with your actual subnet resource ID
param subnetResourceId = '/subscriptions/63862159-43c8-47f7-9f6f-6c63d56b0e17/resourceGroups/rg-hub-rtaudioagent-localdev/providers/Microsoft.Network/virtualNetworks/vnet-hub-rtaudioagent-localdev/subnets/loadBalancer'

// Replace with your actual public IP resource ID
// param publicIpResourceId = '/subscriptions/63862159-43c8-47f7-9f6f-6c63d56b0e17/resourceGroups/ai-realtime-sandbox/providers/Microsoft.Network/publicIPAddresses/ai-realtime-sandbox-appgw-pip'

param privateIpAddress = ''
param enableHttp2 = true

// ============================================================================
// SSL CONFIGURATION - DISABLED
// ============================================================================

param enableSslCertificate = true
param keyVaultSecretId = 'https://kv-rtaudio-devops.vault.azure.net/secrets/rtaudio-fullchain/1e9d85a97f634239a8562418a92766d8'
param sslCertificateName = 'rtaudio-fullchain'
param managedIdentityResourceId = '/subscriptions/63862159-43c8-47f7-9f6f-6c63d56b0e17/resourceGroups/agw-test/providers/Microsoft.ManagedIdentity/userAssignedIdentities/agw-uai-kv-ssl-manual'

// ============================================================================
// BACKEND CONFIGURATION
// ============================================================================

param containerAppBackends = [
  {
    name: 'rtaudioagent-backend'
    fqdn: 'rtaudioagent-backend.eastus2.azurecontainerapps.io'
    port: 80
    protocol: 'Http'
    healthProbePath: '/health'
    healthProbeProtocol: 'Http'
  }
  {
    name: 'rtaudioagent-frontend'
    fqdn: 'rtaudioagent-frontend.eastus2.azurecontainerapps.io'
    port: 80
    protocol: 'Http'
    healthProbePath: '/'
    healthProbeProtocol: 'Http'
  }
]

param additionalBackends = []

// ============================================================================
// ROUTING CONFIGURATION
// ============================================================================

// param frontendPorts = [
//   {
//     name: 'port-80'
//     port: 80
//   }
// ]

param enableHttpRedirect = false
param requestTimeout = 30

// ============================================================================
// WAF CONFIGURATION - FIXED SCHEMA
// ============================================================================

param enableWaf = true
param wafPolicyName = 'waf-rtaudioagent-dev'
param wafMode = 'Detection'
param wafPolicyState = 'Enabled'
param wafRequestBodyCheck = true
param wafMaxRequestBodySizeInKb = 128
param wafFileUploadLimitInMb = 100
param owaspRuleSetVersion = '3.2'


// ============================================================================
// MONITORING CONFIGURATION
// ============================================================================

param enableTelemetry = true

// Replace with your actual Log Analytics workspace resource ID
param logAnalyticsWorkspaceResourceId = '/subscriptions/63862159-43c8-47f7-9f6f-6c63d56b0e17/resourcegroups/rg-hub-rtaudioagent-localdev/providers/microsoft.operationalinsights/workspaces/log-lst5xr4yv7h44'

// ============================================================================
// TAGS
// ============================================================================

param tags = {
  Environment: 'Development'
  Application: 'RTAudioAgent'
  Component: 'ApplicationGateway'
  Owner: 'DevOps Team'
  CostCenter: 'IT-001'
  'azd-env-name': 'dev'
  'hidden-title': 'Real Time Audio Agent Application Gateway'
}
