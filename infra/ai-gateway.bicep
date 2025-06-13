/*
  This module deploys AI Gateway infrastructure, including:
  - Azure AI Services (OpenAI) with multiple model deployments
  - API Management (APIM) for managing APIs
  - Role assignments for APIM to access AI services
*/

import { ModelDeployment, BackendConfigItem } from './modules/types.bicep'

// Parameters
@description('The name of the deployment or resource.')
param name string

param privateDNSZoneResourceId string = ''

@description('The location where the resources will be deployed. Defaults to the resource group location.')
param location string = resourceGroup().location

@description('The environment for the deployment (e.g., dev, test, prod).')
param env string?

@description('Key Vault ID to store secrets.')
param keyVaultResourceId string

@description('Flag to enable or disable the use of a system-assigned managed identity.')
param enableSystemAssignedIdentity bool = true

@description('An array of user-assigned managed identity resource IDs to be used.')
param userAssignedResourceIds array?

@description('An array of diagnostic settings to configure for the resources.')
param diagnosticSettings array = []

@description('The email address of the API Management publisher.')
param apimPublisherEmail string = 'noreply@microsoft.com'

@description('The name of the API Management publisher.')
param apimPublisherName string = 'Microsoft'

@description('Flag to enable API Management for AI Services')
param enableAPIManagement bool = false

param openAIAPISpecURL string = 'https://raw.githubusercontent.com/Azure/azure-rest-api-specs/main/specification/cognitiveservices/data-plane/AzureOpenAI/inference/stable/2024-02-01/inference.json'

@allowed(['S0'])
param aiSvcSku string = 'S0'

@allowed(['BasicV2', 'StandardV2'])
param apimSku string = 'StandardV2'

param namedValues array = []

import { lockType } from 'br/public:avm/utl/types/avm-common-types:0.4.1'
param lock lockType = {
  name: null
  kind: env == 'prod' ? 'CanNotDelete' : 'None'
}

param tags object = {}

var resourceSuffix = uniqueString(subscription().id, resourceGroup().id)

// Backend Configuration with new structure
@description('Array of backend configurations for the AI services.')
param backendConfig BackendConfigItem[]
param aiServiceKind string = 'OpenAI' // Kind of AI service, default is OpenAI

param disableLocalAuth bool = true // Keep enabled for now, can be disabled in prod

param privateEndpoints array = []
// AI Services Deployment with updated model structure
@batchSize(1)
module aiSvc 'br/public:avm/res/cognitive-services/account:0.11.0' = [for (backend, i) in backendConfig: {
  name: 'aiServices-${i}-${resourceSuffix}-${backend.location}'
  params: {
    // Required parameters
    kind: aiServiceKind
    name: 'aisvc-${i}-${resourceSuffix}-${backend.location}'
    // Non-required parameters
    disableLocalAuth: disableLocalAuth
    location: location
    secretsExportConfiguration: disableLocalAuth ? null : {
      accessKey1Name: 'aisvc-${i}-${resourceSuffix}-${backend.location}-accessKey1'
      keyVaultResourceId: keyVaultResourceId
    }
    deployments: [ for model in backend.models: {
        model: {
          format: 'OpenAI'
          name: model.name
          version: model.version
        }
        name: model.name
        sku: {
          name: model.sku
          capacity: model.capacity
        }
      }
    ]
    customSubDomainName: 'aisvc-${i}-${resourceSuffix}-${backend.location}'
    privateEndpoints:privateEndpoints
    publicNetworkAccess: 'Disabled'

    diagnosticSettings: diagnosticSettings
  }
  
}]



// module aiSvc './modules/ai/ai-services-enhanced.bicep' = [for (backend, i) in backendConfig: {
//   name: 'aigw-ai-services-${i}-${backend.location}'
//   params: {
//     name: '${backend.name}-${resourceSuffix}'
//     location: backend.location
//     sku: backend.?sku ?? aiSvcSku
//     tags: tags
//     // Pass the models array directly from the backend config
//     models: backend.models
//     // Private networking configuration
//     servicesSubnetResourceId: servicesSubnetResourceId
//     enablePrivateEndpoint: backend.?enablePrivateEndpoint ?? false
//     customSubdomainName: backend.?customDomain
//     networkAcls: backend.?networkAcls ?? {
//       defaultAction: 'Allow'
//       ipRules: []
//       virtualNetworkRules: []
//     }
//     // Monitoring
//     diagnosticSettings: diagnosticSettings
//     // Key Vault
//     keyVaultResourceId: keyVaultResourceId
//   }
// }]

var formattedApimName = length('apim-${name}-${resourceSuffix}') <= 50
      ? 'apim-${name}-${resourceSuffix}'
      : 'apim-${substring(name, 0, 50 - length('apim--${resourceSuffix}'))}-${resourceSuffix}'


// API Management Deployment
module apim 'br/public:avm/res/api-management/service:0.9.1' = if (enableAPIManagement) {
  name: formattedApimName
  params: {
    name: formattedApimName
    publisherEmail: apimPublisherEmail
    publisherName: apimPublisherName
    location: location
    sku: apimSku
    namedValues: namedValues
    lock: lock
    managedIdentities: {
      systemAssigned: enableSystemAssignedIdentity
      userAssignedResourceIds: userAssignedResourceIds
    }
    diagnosticSettings: diagnosticSettings
    tags: tags

    apis: [
      {
        apiVersionSetName: 'openai-version-set'
        displayName: 'OpenAI API'
        name: 'openai'
        path: 'openai'
        protocols: [
          'http'
          'https'
        ]
        subscriptionRequired: true
        subscriptionKeyParameterNames: {
          header: 'api-key'
          query: 'api-key'
        }
        type: 'http'
        value: openAIAPISpecURL
      }
    ]
    
    backends: [
      for (backend, i) in backendConfig: {
        name: backend.name
        tls: {
          validateCertificateChain: true
          validateCertificateName: false
        }
        url: aiSvc[i].outputs.endpoint
      }
    ]

    apiDiagnostics: [
      {
        apiName: 'openai'
        loggerName: 'oaiLogger'
        metrics: true
        name: 'applicationinsights'
      }
    ]


    policies: [
      {
        format: 'xml'
        value: loadTextContent('./modules/apim/policies/openAI/inbound.xml')
      }
    ]
  }
}

resource _apim 'Microsoft.ApiManagement/service@2024-05-01' existing = if (enableAPIManagement) {
  name: apim.name
}


// Create the backend pool
resource backendPool 'Microsoft.ApiManagement/service/backends@2023-09-01-preview' = {
  name: 'openai-backend-pool'
  parent: _apim
  properties: {
    description: 'Backend pool for Azure OpenAI'
    type: 'Pool'
    pool: {
    services: [for (backend, i) in backendConfig: {
      id: '/backends/${backend.name}'
      priority: backend.priority
      weight: min(backend.?weight ?? 10, 100)
    }]
    }
  }
  dependsOn: [apim]
}


// Role Assignments for APIM
resource apimSystemMIDRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (enableAPIManagement && enableSystemAssignedIdentity) {
  name: guid(resourceGroup().id, _apim.id, 'Azure-AI-Developer')
  scope: resourceGroup()
  properties: {
    principalId: _apim.identity.principalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '64702f94-c441-49e6-a78b-ef80e0188fee')
    principalType: 'ServicePrincipal'
  }
  dependsOn: [aiSvc]
}

// Backend Pools with updated structure
module openAIBackendPool './modules/apim/backend.bicep' = if (enableAPIManagement) {
  name: 'module-api-backend-oai-${resourceSuffix}'
  params: {
    apimName: _apim.name
    backendInstances: [for (backend, i) in backendConfig: {
      name: 'oai-${backend.name}'
      priority: backend.priority
      url: aiSvc[i].outputs.endpoint
      description: '${backend.name} OpenAI endpoint with priority ${backend.priority} in ${backend.location}'
      weight: backend.?weight ?? 10
    }]
    backendPoolName: 'openai-backend-pool'
  }
}



// resource apimDnsZone 'Microsoft.Network/privateDnsZones@2024-06-01' existing = {
//   name: '${apim.name}.azure-api.net'
// }


// resource gatewayRecord 'Microsoft.Network/privateDnsZones/A@2024-06-01' = {
//   parent: apimDnsZone
//   name: '@'
//   dependsOn: [
//     apim
//   ]
//   properties: {
//     aRecords: [
//       {
//         ipv4Address: _apim.properties.privateIPAddresses[0]
//       }
//     ]
//     ttl: 36000
//   }
// }

// resource developerRecord 'Microsoft.Network/privateDnsZones/A@2024-06-01' = {
//   parent: apimDnsZone
//   name: 'developer'
//   dependsOn: [
//     apim
//   ]
//   properties: {
//     aRecords: [
//       {
//         ipv4Address: _apim.properties.privateIPAddresses[0]
//       }
//     ]
//     ttl: 36000
//   }
// }
// APIs
// module openAiApi './modules/apim/api.bicep' = if (enableAPIManagement) {
//   name: 'api-openai-${resourceSuffix}'
//   params: {
//     apimName: _apim.name
//     name: 'api-openai-${name}-${resourceSuffix}'
//     apiPath: 'openai'
//     apiDescription: 'Azure OpenAI API'
//     apiDisplayName: 'Auto Auth OpenAI API'
//     apiSpecURL: openAIAPISpecURL
//     policyContent: loadTextContent('./modules/apim/policies/openAI/inbound.xml')
//     apiSubscriptionName: 'openai-subscription'
//     apiSubscriptionDescription: 'AutoAuth OpenAI Subscription'
//   }
// }

// // User Assigned Identity Role Assignments
// resource uaiAIServiceRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for (id, i) in (userAssignedResourceIds ?? []): {
//   name: guid(resourceGroup().id, id, 'Azure-AI-Developer')
//   scope: resourceGroup()
//   properties: {
//     principalId: id
//     roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '64702f94-c441-49e6-a78b-ef80e0188fee')
//     principalType: 'ServicePrincipal'
//   }
//   dependsOn: [aiSvc]
// }]

// ========== Outputs ==========
@description('API Management service details')
output apim object = enableAPIManagement ? {
  name: _apim.name
  id: _apim.id
  location: _apim.location
  sku: _apim.sku.name
  publisherEmail: _apim.properties.publisherEmail
  publisherName: _apim.properties.publisherName
  gatewayUrl: _apim.properties.gatewayUrl
  identity: _apim.identity
} : {}

@description('API endpoint URLs for accessing AI services')
output endpoints object = {
  openAI: enableAPIManagement 
    ? '${_apim.properties.gatewayUrl}/openai'
    : length(backendConfig) > 0 ? aiSvc[0].outputs.endpoints['OpenAI Language Model Instance API'] : ''
}

// @description('Authentication details for accessing the APIs')
// output authentication object = {
//   subscriptionKeys: {
//     openAI: enableAPIManagement 
//       ? openAiApi.outputs.apiSubscriptionKey 
//       : length(backendConfig) > 0 ? aiSvc[0].outputs.: ''
//   }
//   managedIdentity: enableAPIManagement ? _apim.identity : {}
// }



@description('Complete AI Services module outputs for advanced scenarios')
output aiSvcRaw array = [for (item, i) in backendConfig: aiSvc[i].outputs]

// Additional outputs for easier access
@description('AI Gateway endpoints for simplified access')
output aiGatewayEndpoints array = [for (item, i) in backendConfig: aiSvc[i].outputs.endpoint]

@description('AI Gateway service IDs')
output aiGatewayServiceIds array = [for (item, i) in backendConfig: aiSvc[i].outputs.resourceId]

