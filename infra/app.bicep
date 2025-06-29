@description('The location used for all deployed resources')
param location string = resourceGroup().location

@description('Tags that will be applied to all resources')
param tags object = {}

@description('Name of the environment that can be used as part of naming resource convention')
param name string

import { ContainerAppKvSecret } from './modules/types.bicep'

// AZD managed variables
param rtaudioClientExists bool
param rtaudioServerExists bool

// Required parameters for the app environment (app config values, secrets, etc.)
@description('Enable EasyAuth for the frontend internet facing container app')
param enableEasyAuth bool = true

param appInsightsConnectionString string = 'InstrumentationKey=00000000-0000-0000-0000-000000000000;IngestionEndpoint=https://dc.services.visualstudio.com/v2/track'
param logAnalyticsWorkspaceResourceId string = '00000000-0000-0000-0000-000000000000'

// Network parameters for reference
// param vnetName string
// param appgwSubnetResourceId string
param appSubnetResourceId string

@description('Id of the user or app to assign application roles')
param principalId string
param principalType string

var abbrs = loadJsonContent('./abbreviations.json')
var resourceToken = uniqueString(subscription().id, resourceGroup().id, location)

param backendUserAssignedIdentity object = {}
param frontendUserAssignedIdentity object = {}

var beContainerName =  toLower(substring('rtagent-server-${resourceToken}', 0, 22))
var feContainerName =  toLower(substring('rtagent-client-${resourceToken}', 0, 22))

// Container registry
module containerRegistry 'br/public:avm/res/container-registry/registry:0.1.1' = {
  name: 'registry'
  params: {
    name: '${name}${abbrs.containerRegistryRegistries}${resourceToken}'
    location: location
    tags: tags
    publicNetworkAccess: 'Enabled'
    roleAssignments: [
      {
        principalId: principalId
        principalType: principalType
        roleDefinitionIdOrName: 'AcrPull'
      }
      {
        principalId: principalId
        principalType: principalType
        roleDefinitionIdOrName: 'AcrPush'
      }
      // Temporarily disabled - managed identity deployment timing issue
      {
        principalId: frontendUserAssignedIdentity.principalId
        principalType: 'ServicePrincipal'
        roleDefinitionIdOrName: 'AcrPull'
      }
      {
        principalId: backendUserAssignedIdentity.principalId
        principalType: 'ServicePrincipal'
        roleDefinitionIdOrName: 'AcrPull'
      }
    ]
  }
}

param frontendExternalAccessEnabled bool = true
// Container apps environment (deployed into appSubnet)
module externalContainerAppsEnvironment 'br/public:avm/res/app/managed-environment:0.11.2' = if (frontendExternalAccessEnabled){
  name: 'external-container-apps-environment'
  params: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: reference(logAnalyticsWorkspaceResourceId, '2022-10-01').customerId
        sharedKey: listKeys(logAnalyticsWorkspaceResourceId, '2022-10-01').primarySharedKey
      }
    }
    publicNetworkAccess: 'Enabled' // Allows public access to the Container Apps Environment
    name: 'ext-${name}${abbrs.appManagedEnvironments}${resourceToken}'
    location: location
    zoneRedundant: false
    // infrastructureSubnetResourceId: appSubnetResourceId // Enables private networking in the specified subnet
    internal: false
    tags: tags
  }
}

param privateDnsZoneResourceId string = ''
param privateEndpointSubnetResourceId string = ''

// Container apps environment (deployed into appSubnet)
module containerAppsEnvironment 'br/public:avm/res/app/managed-environment:0.11.2' = {
  name: 'container-apps-environment'
  params: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: reference(logAnalyticsWorkspaceResourceId, '2022-10-01').customerId
        sharedKey: listKeys(logAnalyticsWorkspaceResourceId, '2022-10-01').primarySharedKey
      }
    }
    name: '${name}${abbrs.appManagedEnvironments}${resourceToken}'
    location: location
    zoneRedundant: false
    infrastructureSubnetResourceId: appSubnetResourceId // Enables private networking in the specified subnet
    internal: appSubnetResourceId != '' ? true : false
    tags: tags
  }
}

// Private endpoint for Container Apps Environment
module containerAppsPrivateEndpoint './modules/networking/private-endpoint.bicep' = if (privateDnsZoneResourceId != '' && privateEndpointSubnetResourceId != '') {
    name: 'backend-container-apps-private-endpoint'
    params: {
      name: 'pe-${name}${resourceToken}'
      location: location
      tags: tags
      subnetId: privateEndpointSubnetResourceId
      serviceId: containerAppsEnvironment.outputs.resourceId
      groupIds: ['managedEnvironments']
      dnsZoneId: privateDnsZoneResourceId
    }
}

param storageSkuName string = 'Standard_LRS'
param storageContainerName string = 'audioagent'


module storage 'br/public:avm/res/storage/storage-account:0.9.1' = {
  name: 'storage'
  params: {
    name: '${abbrs.storageStorageAccounts}${resourceToken}'
    location: location
    tags: tags
    kind: 'StorageV2'
    skuName: storageSkuName
    publicNetworkAccess: 'Enabled' // Necessary for uploading documents to storage container
    networkAcls: {
      defaultAction: 'Allow'
      bypass: 'AzureServices'
    }
    allowBlobPublicAccess: false
    allowSharedKeyAccess: true
    blobServices: {
      deleteRetentionPolicyDays: 2
      deleteRetentionPolicyEnabled: true
      containers: [
        {
          name: storageContainerName
          publicAccess: 'None'
        }
        {
          name: 'prompt'
          publicAccess: 'None'
        }
      ]
    }
    roleAssignments: [
      {
        roleDefinitionIdOrName: 'Storage Blob Data Contributor'
        principalId: backendUserAssignedIdentity.principalId
        principalType: 'ServicePrincipal'
      }
      {
        roleDefinitionIdOrName: 'Storage Blob Data Reader'
        principalId: principalId
        // principalType: 'User'  
        principalType: principalType
      } 
      {
        roleDefinitionIdOrName: 'Storage Blob Data Contributor'
        principalId: principalId
        // principalType: 'User'
        principalType: principalType
      }      
    ]
  }
}

module fetchFrontendLatestImage './modules/app/fetch-container-image.bicep' = {
  name: 'gbbAiAudioAgent-fetch-image'
  params: {
    exists: rtaudioClientExists
    name: feContainerName
  }
}
module fetchBackendLatestImage './modules/app/fetch-container-image.bicep' = {
  name: 'gbbAiAudioAgentBackend-fetch-image'
  params: {
    exists: rtaudioServerExists
    name: beContainerName
  }
}

module frontendAudioAgent 'modules/app/container-app.bicep' = {
  name: 'frontend-audio-agent'
  params: {
    name: feContainerName
    enableEasyAuth: enableEasyAuth
    corsPolicy: {
      allowedOrigins: [
        'http://localhost:5173'
        'http://localhost:3000'
      ]
      allowedMethods: ['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS']
      allowedHeaders: ['*']
      allowCredentials: false
    }
    
    publicAccessAllowed: true

    ingressTargetPort: 5173
    scaleMinReplicas: 1
    scaleMaxReplicas: 10
    stickySessionsAffinity: 'sticky'
    containers: [
      {
        image: fetchFrontendLatestImage.outputs.?containers[?0].?image ?? 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
        name: 'main'
        resources: {
          cpu: json('0.5')
          memory: '1.0Gi'
        }
        env: [
          {
            name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
            value: appInsightsConnectionString
          }
          {
            name: 'AZURE_CLIENT_ID'
            value: frontendUserAssignedIdentity.clientId
          }
          {
            name: 'PORT'
            value: '5173'
          }
          // {
          //   name: 'VITE_BACKEND_BASE_URL'
          //   value: 'https://${existingAppGatewayPublicIp.properties.dnsSettings.fqdn}'
          // }
        ]
      }
    ]
    userAssignedResourceId: frontendUserAssignedIdentity.resourceId

    registries: [
      {
        server: containerRegistry.outputs.loginServer
        identity: frontendUserAssignedIdentity.resourceId
      }
    ]
    
    environmentResourceId: frontendExternalAccessEnabled ? externalContainerAppsEnvironment.outputs.resourceId : containerAppsEnvironment.outputs.resourceId
    // environmentResourceId: containerAppsEnvironment.outputs.resourceId
    location: location
    tags: union(tags, { 'azd-service-name': 'rtaudio-client' })
  }
  dependsOn: [
  ]
}


param backendSecrets ContainerAppKvSecret[] 
param backendEnvVars array = []
module backendAudioAgent './modules/app/container-app.bicep' = {
  name: 'backend-audio-agent'
  params: {
    name: beContainerName
    ingressTargetPort: 8010
    scaleMinReplicas: 1
    scaleMaxReplicas: 10
    secrets: backendSecrets
    corsPolicy: {
      allowedOrigins: [
      // 'https://${frontendAudioAgent.outputs.containerAppFqdn}'
      // 'https://${existingAppGatewayPublicIp.properties.dnsSettings.fqdn}'
      // 'https://${existingAppGatewayPublicIp.properties.ipAddress}'
      'http://localhost:5173'
      ]
      allowedMethods: ['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS']
      allowedHeaders: ['*']
      allowCredentials: true
    }
    containers: [
      {
        image: fetchBackendLatestImage.outputs.?containers[?0].?image ?? 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
        name: 'main'
        resources: {
          cpu: json('1.0')
          memory: '2.0Gi'
        }

        env: backendEnvVars
      }
    ]
    userAssignedResourceId: backendUserAssignedIdentity.?resourceId ?? ''
    registries: [
      {
        server: containerRegistry.outputs.loginServer
        identity: backendUserAssignedIdentity.?resourceId ?? ''
      }
    ]
    environmentResourceId: containerAppsEnvironment.outputs.resourceId
    location: location
    tags: union(tags, { 'azd-service-name': 'rtaudio-server' })
  }
}


// Outputs for downstream consumption and integration

// Container Registry
output containerRegistryEndpoint string = containerRegistry.outputs.loginServer
output containerRegistryResourceId string = containerRegistry.outputs.resourceId

// Container Apps Environment
output containerAppsEnvironmentId string = containerAppsEnvironment.outputs.resourceId

// Container Apps
// output frontendContainerAppResourceId string = frontendAudioAgent.outputs.containerAppResourceId
output backendContainerAppResourceId string = backendAudioAgent.outputs.containerAppResourceId
output frontendAppName string = feContainerName
output backendAppName string = beContainerName

output frontendContainerAppFqdn string = frontendAudioAgent.outputs.containerAppFqdn
output backendContainerAppFqdn string = backendAudioAgent.outputs.containerAppFqdn

// NOTE: These parameters are currently not used directly in this file, but are available for future use and for passing to modules that support subnet assignment.
