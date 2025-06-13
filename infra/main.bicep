targetScope = 'subscription'

@minLength(1)
@maxLength(64)
@description('Name of the environment that can be used as part of naming resource convention')
param environmentName string

param name string = 'rtaudioagent'

@minLength(1)
@description('Primary location for all resources')
param location string

import { ModelConfig, SubnetConfig, BackendConfigItem } from './modules/types.bicep'

param gbbAiAudioAgentExists bool
param gbbAiAudioAgentBackendExists bool

@description('Flag to enable/disable the use of APIM for OpenAI loadbalancing')
param enableAPIManagement bool = true

@description('Id of the user or app to assign application roles')
param principalId string

// param acsSourcePhoneNumber string
@description('[Required when enableAPIManagement is true] Array of backend configurations for the AI services.')
param azureOpenAIBackendConfig BackendConfigItem[]


// @secure()
// @description('Base64-encoded Root SSL certificate (.cer) for Application Gateway')
// param rootCertificateBase64Value string

var abbrs = loadJsonContent('./abbreviations.json')
var resourceToken = uniqueString(subscription().id, rg.id, location)

// Network Config
// -----------------------------------------------------------
@description('Address space for the Virtual Network')
param vnetAddressPrefix string = '10.0.0.0/16'

var subnets = [
  {
    name: 'loadBalancer'
    addressPrefix: '10.0.1.0/26'
  }
  {
    name: 'apim'
    addressPrefix: '10.0.2.0/26'
  }
  {
    name: 'privateEndpoint'
    addressPrefix: '10.0.3.0/26'
  }
  {
    name: 'app'
    addressPrefix: '10.0.10.0/27'
  }
  {
    name: 'cache'
    addressPrefix: '10.0.11.0/27'
  }
  {
    name: 'services'
    addressPrefix: '10.0.12.0/26'
  }
  {
    name: 'jumpbox'
    addressPrefix: '10.0.13.0/27'
  }
]


// Tags that should be applied to all resources.
// 
// Note that 'azd-service-name' tags should be applied separately to service host resources.
// Example usage:
//   tags: union(tags, { 'azd-service-name': <service name in azure.yaml> })
var tags = {
  'azd-env-name': environmentName
  'hidden-title': 'Real Time Audio ${environmentName}'

}

// Organize resources in a resource group
resource rg 'Microsoft.Resources/resourceGroups@2021-04-01' = {
  name: 'rg-${name}-${environmentName}'
  location: location
  tags: tags
}


// Monitor application with Azure Monitor
module monitoring 'br/public:avm/ptn/azd/monitoring:0.1.0' = {
  name: 'monitoring'
  scope: rg
  params: {
    logAnalyticsName: '${abbrs.operationalInsightsWorkspaces}${resourceToken}'
    applicationInsightsName: '${abbrs.insightsComponents}${resourceToken}'
    applicationInsightsDashboardName: '${abbrs.portalDashboards}${resourceToken}'
    location: location
    tags: tags
  }
}

module network 'network.bicep' = {
  scope: rg
  name: 'network'
  params: {
    location: location
    tags: tags
    vnetName: 'vnet-${name}-${environmentName}'
    vnetAddressPrefix: vnetAddressPrefix
    subnets: subnets
    enablePrivateDnsZone: true // Enable Private DNS Zone for OpenAI
    workspaceResourceId: monitoring.outputs.logAnalyticsWorkspaceResourceId
    // Optionally, you can pass custom subnet configs or domain label here if needed
  }
}

// Key Vault for storing secrets
module keyVault 'br/public:avm/res/key-vault/vault:0.12.1' = {
  name: 'kv-${name}-${environmentName}-${resourceToken}'
  scope: rg
  params: {
    name: '${abbrs.keyVaultVaults}${resourceToken}'
    location: location
    tags: tags
    enableRbacAuthorization: true
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      defaultAction: 'Allow'
      bypass: 'AzureServices'
    }
    roleAssignments: [
      {
        principalId: principalId
        principalType: 'User'
        roleDefinitionIdOrName: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '00482a5a-887f-4fb3-b363-3b7fe8e74483') // Key Vault Administrator
      }
    ]
    diagnosticSettings: [
      {
        name: 'default'
        workspaceResourceId: monitoring.outputs.logAnalyticsWorkspaceResourceId
        logCategoriesAndGroups: [
          {
            categoryGroup: 'audit'
            enabled: true
          }
          {
            categoryGroup: 'allLogs'
            enabled: true
          }
        ]
        metricCategories: [
          {
            category: 'AllMetrics'
            enabled: true
          }
        ]
      }
    ]
  }
}

module aiGateway 'ai-gateway.bicep' = {
  scope: rg
  name: 'ai-gateway'
  params: {
    name: name
    enableAPIManagement: enableAPIManagement
    location: location
    tags: tags
    apimSku: 'StandardV2'
    backendConfig: azureOpenAIBackendConfig
    keyVaultResourceId: keyVault.outputs.resourceId
    privateEndpoints: [
      {
        privateDnsZoneGroup: {
          privateDnsZoneGroupConfigs: [
            {
              privateDnsZoneResourceId: network.outputs.privateDnsZoneId
            }
          ]
        }
        subnetResourceId: network.outputs.subnets.privateEndpoint
      }
    ]
    // Pass monitoring config from monitoring module
    diagnosticSettings: [
      {
        name: 'default'
        workspaceResourceId: monitoring.outputs.logAnalyticsWorkspaceResourceId
        logCategoriesAndGroups: [
          {
            categoryGroup: 'audit'
            enabled: true
          }
          {
            categoryGroup: 'allLogs'
            enabled: true
          }
        ]
        metricCategories: [
          {
            category: 'AllMetrics'
            enabled: true
          }
        ]
      }
      
    ]
  }
}



// module app 'app.bicep' = {
//   scope: rg
//   name: 'app'
//   params: {
//     name: name
//     location: location
//     tags: tags

//     keyVaultResourceId: keyVault.outputs.resourceId

//     aoai_endpoint: aiGateway.outputs.endpoints.openAI
//     aoai_chat_deployment_id: 'gpt-4o-standard'
//     // Monitoring
//     appInsightsConnectionString: monitoring.outputs.applicationInsightsConnectionString
//     logAnalyticsWorkspaceResourceId: monitoring.outputs.logAnalyticsWorkspaceResourceId

//     // Managed by AZD to deploy code to container apps
//     acsSourcePhoneNumber: acsSourcePhoneNumber
//     gbbAiAudioAgentExists: gbbAiAudioAgentExists
//     gbbAiAudioAgentBackendExists: gbbAiAudioAgentBackendExists

//     // Network configuration from network module
//     vnetName: network.outputs.vnetName
//     appgwSubnetResourceId: network.outputs.appgwSubnetResourceId
//     appSubnetResourceId: network.outputs.backendSubnetResourceId
//   }
// }

// module loadbalancer 'loadbalancer.bicep' = {
//   scope: rg
//   name: 'loadbalancer'
//   params: {
//     location: location
//     tags: tags
//     vnetName: network.outputs.vnetName
//     subnetResourceIds: network.outputs.subnetResourceIds
//     enableAppGateway: false // Set to true if you want to enable Application Gateway
//     appGatewaySku: 'Standard_v2'
//     backendFqdn: app.outputs.backendBaseUrl
//     publicIpResourceId: network.outputs.publicIpResourceId
//     sslCertBase64: rootCertificateBase64Value
//   }
// }

// output containerRegistryEndpoint string = app.outputs.containerRegistryEndpoint
// output containerRegistryResourceId string = app.outputs.containerRegistryResourceId
// output containerAppsEnvironmentId string = app.outputs.containerAppsEnvironmentId
// output frontendUserAssignedIdentityClientId string = app.outputs.frontendUserAssignedIdentityClientId
// output frontendUserAssignedIdentityResourceId string = app.outputs.frontendUserAssignedIdentityResourceId
// output backendUserAssignedIdentityClientId string = app.outputs.backendUserAssignedIdentityClientId
// output backendUserAssignedIdentityResourceId string = app.outputs.backendUserAssignedIdentityResourceId
// output communicationServicesResourceId string = app.outputs.communicationServicesResourceId
// output communicationServicesEndpoint string = app.outputs.communicationServicesEndpoint
// output aiGatewayEndpoints array = aiGateway.outputs.aiGatewayEndpoints
// output aiGatewayServiceIds array = aiGateway.outputs.aiGatewayServiceIds
// output frontendContainerAppResourceId string = app.outputs.frontendContainerAppResourceId
// output backendContainerAppResourceId string = app.outputs.backendContainerAppResourceId
// output frontendAppName string = app.outputs.frontendAppName
// output backendAppName string = app.outputs.backendAppName
// output frontendBaseUrl string = app.outputs.frontendBaseUrl
// output backendBaseUrl string = app.outputs.backendBaseUrl
