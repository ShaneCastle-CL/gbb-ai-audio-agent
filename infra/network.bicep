@description('The location for all resources')
param location string = resourceGroup().location

@description('Name of the Virtual Network')
param vnetName string 

import { SubnetConfig } from './modules/types.bicep'

@description('Address space for the Virtual Network')
param vnetAddressPrefix string

@description('List of Subnet configurations')
param subnets SubnetConfig[]

@description('Tags to apply to all resources')
param tags object = {}

@description('Enable creation of Private DNS Zone')
param enablePrivateDnsZone bool = false

@description('Private DNS Zone name')
param privateDnsZoneName string = 'privatelink.openai.azure.com'

@description('Domain label for the public IP address (<domainlabel>.<location>.cloudapp.azure.com)')
param pipDomainLabel string = toLower('rtaudio-pip-${uniqueString(resourceGroup().id, vnetName)}')
@description('Resource ID of the Log Analytics workspace for diagnostics')
param workspaceResourceId string

resource vnet 'Microsoft.Network/virtualNetworks@2023-02-01' = {
  name: vnetName
  location: location
  tags: tags
  properties: {
    addressSpace: {
      addressPrefixes: [
        vnetAddressPrefix
      ]
    }
    subnets: [for subnet in subnets: {
      name: subnet.name
      properties: {
        addressPrefix: subnet.addressPrefix
      }
    }]
  }
}

var subnetResourceIds = [for subnet in subnets: resourceId('Microsoft.Network/virtualNetworks/subnets', vnetName, subnet.name)]

resource publicIp 'Microsoft.Network/publicIPAddresses@2023-02-01' = {
  name: 'pip-${vnetName}'
  location: location
  tags: tags
  sku: {
    name: 'Standard'
  }
  properties: {
    publicIPAllocationMethod: 'Static'
    dnsSettings: {
      domainNameLabel: pipDomainLabel
    }
  }
}

// Diagnostic settings for VNet
resource vnetDiagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'diag-${vnet.name}'
  scope: vnet
  properties: {
    workspaceId: workspaceResourceId
    logs: [
      {
        categoryGroup: 'allLogs'
        enabled: true
      }
    ]
    metrics: [
      {
        category: 'AllMetrics'
        enabled: true
      }
    ]
  }
}

// Diagnostic settings for Public IP
resource publicIpDiagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'diag-${publicIp.name}'
  scope: publicIp
  properties: {
    workspaceId: workspaceResourceId
    logs: [
      {
        categoryGroup: 'allLogs'
        enabled: true
      }
    ]
    metrics: [
      {
        category: 'AllMetrics'
        enabled: true
      }
    ]
  }
}

// Output the FQDN and IP address of the public IP for downstream consumption
// Output the FQDN, IP address, and resource ID of the public IP for downstream consumption
output publicIpFqdn string = publicIp.properties.dnsSettings.fqdn
output publicIpAddress string = publicIp.properties.ipAddress
output publicIpResourceId string = publicIp.id


resource privateDnsZone 'Microsoft.Network/privateDnsZones@2020-06-01' = if (enablePrivateDnsZone) {
  name: privateDnsZoneName
  location: 'global'
  tags: tags
}

resource vnetLinks 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  name: vnet.name
  parent: privateDnsZone
  location: 'global'
  properties: {
    virtualNetwork: {
      id: vnet.id
    }
    registrationEnabled: false
  }
}


// Output VNet and subnet details for downstream modules
output vnetId string = vnet.id
output vnetName string = vnet.name
output subnetResourceIds array = subnetResourceIds
output subnetNames array = [for subnet in subnets: subnet.name]
output subnets object = toObject(subnets, subnet => subnet.name, subnet => resourceId('Microsoft.Network/virtualNetworks/subnets', vnetName, subnet.name))

output privateDnsZoneId string = enablePrivateDnsZone ? privateDnsZone.id : ''
output privateDnsZoneName string = privateDnsZone.name
output vnetLinksLink string = vnetLinks.id
