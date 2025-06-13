/*
  Private Endpoint Module
  
  This module creates a private endpoint with DNS integration
*/

@description('Name of the private endpoint')
param name string

@description('Location for the private endpoint')
param location string

@description('Tags to apply to resources')
param tags object = {}

@description('Resource ID of the private link service')
param privateLinkServiceId string

@description('Group IDs for the private endpoint')
param groupIds array

@description('Subnet resource ID for the private endpoint')
param subnetResourceId string

@description('Private DNS zone name')
param privateDnsZoneName string

// Private Endpoint
resource privateEndpoint 'Microsoft.Network/privateEndpoints@2023-09-01' = {
  name: name
  location: location
  tags: tags
  properties: {
    subnet: {
      id: subnetResourceId
    }
    privateLinkServiceConnections: [
      {
        name: '${name}-connection'
        properties: {
          privateLinkServiceId: privateLinkServiceId
          groupIds: groupIds
        }
      }
    ]
  }
}

// Private DNS Zone Group
resource privateDnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-09-01' = {
  parent: privateEndpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: replace(privateDnsZoneName, '.', '-')
        properties: {
          privateDnsZoneId: resourceId('Microsoft.Network/privateDnsZones', privateDnsZoneName)
        }
      }
    ]
  }
}

// Outputs
output privateEndpointId string = privateEndpoint.id
output privateEndpointName string = privateEndpoint.name
output privateIpAddress string = privateEndpoint.properties.customDnsConfigs[0].ipAddresses[0]
