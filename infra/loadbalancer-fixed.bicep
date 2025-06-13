targetScope = 'resourceGroup'

// ==========================================
// PARAMETERS
// ==========================================

// Core Parameters
@description('Enable Application Gateway deployment')
param enableAppGateway bool = true

@description('Location for Application Gateway')
param location string = resourceGroup().location

@description('Application Gateway name')
param appGatewayName string

@description('Subnet resource ID for Application Gateway')
param subnetResourceId string

@description('Public IP resource ID for frontend')
param publicIpResourceId string

@description('Tags to apply to all resources')
param tags object = {}

// SKU and Capacity Parameters
@description('Application Gateway SKU')
@allowed([
  'Standard_v2'
  'WAF_v2'
])
param appGatewaySku string = 'WAF_v2'

@description('Application Gateway tier')
@allowed([
  'Standard_v2'
  'WAF_v2'
])
param appGatewayTier string = 'WAF_v2'

@description('Application Gateway capacity configuration')
param capacity object = {
  minCapacity: 0
  maxCapacity: 10
}

@description('Availability zones for Application Gateway')
param availabilityZones array = ['1', '2', '3']

// SSL/TLS Configuration
@description('SSL certificates configuration')
param sslCertificates array = [
  // {
  //   name: 'root'
  //   keyVaultSecretId: 'https://your-keyvault.vault.azure.net/secrets/root-certificate'
  // }
  // {
  //   name: 'server'
  //   keyVaultSecretId: 'https://your-keyvault.vault.azure.net/secrets/server-certificate'
  // }
]

@description('Trusted root certificates configuration')
param trustedRootCertificates array = [
  // {
  //   name: 'root'
  //   keyVaultSecretId: 'https://your-keyvault.vault.azure.net/secrets/root-trusted-certificate'
  // }
  // {
  //   name: 'aca-ms-root-cert'
  //   keyVaultSecretId: 'https://your-keyvault.vault.azure.net/secrets/aca-ms-root-certificate'
  // }
]

// Frontend Configuration
@description('Frontend ports configuration')
param frontendPorts array = [
  {
    name: 'port_80'
    port: 80
  }
  {
    name: 'port_443'
    port: 443
  }
  {
    name: 'port_445'
    port: 445
  }
]

// Backend Configuration
@description('Backend pools configuration')
param backendPools array = [
  {
    name: 'rtaudio-private-sandbox-eus2'
    fqdns: ['rtaudio-private-sandbox-eus2.blackwater-3c853cba.eastus2.azurecontainerapps.io']
    ipAddresses: []
  }
  {
    name: 'dummypool'
    fqdns: []
    ipAddresses: []
  }
]

@description('Backend HTTP settings configuration')
param backendHttpSettings array = [
  {
    name: 'priv-be-ws-setting-https'
    port: 443
    protocol: 'Https'
    cookieBasedAffinity: 'Disabled'
    requestTimeout: 20
    connectionDraining: {
      enabled: true
      drainTimeoutInSec: 60
    }
    pickHostNameFromBackendAddress: true
    path: ''
    probeName: 'priv-be-https-probe'
    trustedRootCertificateNames: ['aca-ms-root-cert']
  }
  {
    name: 'dummypool'
    port: 80
    protocol: 'Http'
    cookieBasedAffinity: 'Disabled'
    requestTimeout: 20
    pickHostNameFromBackendAddress: false
    path: ''
  }
  {
    name: 'priv-be-ws-setting-http-8010'
    port: 8010
    protocol: 'Http'
    cookieBasedAffinity: 'Enabled'
    requestTimeout: 1200
    connectionDraining: {
      enabled: true
      drainTimeoutInSec: 200
    }
    pickHostNameFromBackendAddress: true
    path: ''
    probeName: 'priv-be-http-probe'
  }
  {
    name: 'apis'
    port: 80
    protocol: 'Http'
    cookieBasedAffinity: 'Disabled'
    requestTimeout: 20
    pickHostNameFromBackendAddress: true
    path: ''
    probeName: 'priv-be-http-probe'
  }
  {
    name: 'websocket'
    port: 80
    protocol: 'Http'
    cookieBasedAffinity: 'Enabled'
    requestTimeout: 200
    connectionDraining: {
      enabled: true
      drainTimeoutInSec: 453
    }
    pickHostNameFromBackendAddress: true
    path: ''
    probeName: 'priv-be-http-probe'
  }
]

// Listener Configuration
@description('HTTP listeners configuration')
param httpListeners array = [
  {
    name: 'priv-be-pathbased-https-listener'
    frontendIPConfigurationName: 'appGwPublicFrontendIpIPv4'
    frontendPortName: 'port_443'
    protocol: 'Https'
    hostName: ''
    requireServerNameIndication: false
    sslCertificateName: 'gd-rtaudiodemo-fullchain'
    firewallPolicyId: ''
  }
]

// Health Probe Configuration
@description('Health probes configuration')
param healthProbes array = [
  {
    name: 'priv-be-https-probe'
    protocol: 'Https'
    host: ''
    path: '/healthz'
    interval: 120
    timeout: 10
    unhealthyThreshold: 3
    pickHostNameFromBackendHttpSettings: true
    minServers: 0
    match: {
      body: ''
      statusCodes: ['200-399']
    }
  }
  {
    name: 'priv-be-http-probe'
    protocol: 'Http'
    host: ''
    path: '/healthz'
    interval: 30
    timeout: 30
    unhealthyThreshold: 3
    pickHostNameFromBackendHttpSettings: true
    minServers: 0
    match: {
      body: ''
      statusCodes: ['200-399']
    }
  }
]

// URL Path Maps Configuration
@description('URL path maps configuration')
param urlPathMaps array = [
  {
    name: 'priv-be-pathbased-https-rule'
    defaultBackendAddressPoolName: 'rtaudio-private-sandbox-eus2'
    defaultBackendHttpSettingsName: 'apis'
    pathRules: [
      {
        name: 'backend-health-probes'
        paths: ['/healthz']
        backendAddressPoolName: 'rtaudio-private-sandbox-eus2'
        backendHttpSettingsName: 'apis'
      }
      {
        name: 'backend-ws'
        paths: ['/realtime-acs', '/realtime', '/call/stream']
        backendAddressPoolName: 'rtaudio-private-sandbox-eus2'
        backendHttpSettingsName: 'websocket'
      }
      {
        name: 'apis'
        paths: ['/api/*', '/call/callbacks']
        backendAddressPoolName: 'rtaudio-private-sandbox-eus2'
        backendHttpSettingsName: 'apis'
      }
    ]
  }
]

// Routing Configuration
@description('Request routing rules configuration')
param requestRoutingRules array = [
  {
    name: 'priv-be-pathbased-https-rule'
    ruleType: 'PathBasedRouting'
    httpListenerName: 'priv-be-pathbased-https-listener'
    urlPathMapName: 'priv-be-pathbased-https-rule'
    priority: 1
  }
]

// WAF Configuration
@description('Web Application Firewall configuration')
param wafConfiguration object = {
  enabled: true
  firewallMode: 'Prevention'
  ruleSetType: 'OWASP'
  ruleSetVersion: '3.0'
  fileUploadLimitInMb: 100
  requestBodyCheck: true
  maxRequestBodySizeInKb: 128
  disabledRuleGroups: []
  exclusions: []
}

@description('SSL policy configuration')
param sslPolicy object = {
  policyType: 'Predefined'
  policyName: 'AppGwSslPolicy20220101'
}

// Additional configurations
@description('Enable HTTP/2')
param enableHttp2 bool = false

@description('Enable FIPS')
param enableFips bool = false

@description('Enable system assigned managed identity')
param enableSystemManagedIdentity bool = false

@description('User assigned managed identity resource IDs')
param userAssignedIdentityIds array = []

// ==========================================
// COMPUTED VARIABLES
// ==========================================

var identityType = enableSystemManagedIdentity && !empty(userAssignedIdentityIds) ? 'SystemAssigned, UserAssigned' 
  : enableSystemManagedIdentity ? 'SystemAssigned' 
  : !empty(userAssignedIdentityIds) ? 'UserAssigned' : 'None'

var userAssignedIdentityDict = !empty(userAssignedIdentityIds) ? reduce(userAssignedIdentityIds, {}, (acc, id) => union(acc, { '${id}': {} })) : {}

// Helper variables for backend address processing
var backendAddressesFlattened = [for pool in backendPools: {
  name: pool.name
  addresses: union(
    map(pool.?fqdns ?? [], fqdn => { fqdn: fqdn }),
    map(pool.?ipAddresses ?? [], ip => { ipAddress: ip })
  )
}]

// ==========================================
// APPLICATION GATEWAY RESOURCE
// ==========================================

resource appGateway 'Microsoft.Network/applicationGateways@2024-05-01' = if (enableAppGateway) {
  name: appGatewayName
  location: location
  tags: tags
  zones: availabilityZones
  identity: identityType != 'None' ? {
    type: identityType
    userAssignedIdentities: !empty(userAssignedIdentityIds) ? userAssignedIdentityDict : null
  } : null
  properties: {
    sku: {
      name: appGatewaySku
      tier: appGatewayTier
    }
    // Autoscale configuration
    autoscaleConfiguration: {
      minCapacity: capacity.minCapacity
      maxCapacity: capacity.maxCapacity
    }
    // Gateway IP configurations
    gatewayIPConfigurations: [
      {
        name: 'appGatewayIpConfig'
        properties: {
          subnet: {
            id: subnetResourceId
          }
        }
      }
    ]
    // Frontend IP configurations
    frontendIPConfigurations: [
      {
        name: 'appGwPublicFrontendIpIPv4'
        properties: {
          privateIPAllocationMethod: 'Dynamic'
          publicIPAddress: {
            id: publicIpResourceId
          }
        }
      }
    ]
    // Frontend ports
    frontendPorts: [for port in frontendPorts: {
      name: port.name
      properties: {
        port: port.port
      }
    }]
    // SSL certificates
    sslCertificates: [for cert in sslCertificates: {
      name: cert.name
      properties: {
        data: cert.?data
        keyVaultSecretId: cert.?keyVaultSecretId
        password: cert.?password
      }
    }]
    // Trusted root certificates
    trustedRootCertificates: [for cert in trustedRootCertificates: {
      name: cert.name
      properties: {
        data: cert.?data
        keyVaultSecretId: cert.?keyVaultSecretId
      }
    }]
    // Backend address pools
    backendAddressPools: [for (pool, i) in backendPools: {
      name: pool.name
      properties: {
        backendAddresses: backendAddressesFlattened[i].addresses
      }
    }]
    // Backend HTTP settings
    backendHttpSettingsCollection: [for setting in backendHttpSettings: {
      name: setting.name
      properties: {
        port: setting.port
        protocol: setting.protocol
        cookieBasedAffinity: setting.?cookieBasedAffinity ?? 'Disabled'
        requestTimeout: setting.?requestTimeout ?? 30
        connectionDraining: setting.?connectionDraining
        pickHostNameFromBackendAddress: setting.?pickHostNameFromBackendAddress ?? false
        hostName: setting.?hostName ?? ''
        path: setting.?path ?? ''
        probe: !empty(setting.?probeName ?? '') ? {
          id: resourceId('Microsoft.Network/applicationGateways/probes', appGatewayName, setting.probeName)
        } : null
        // trustedRootCertificates: !empty(setting.?trustedRootCertificateNames ?? []) ? [for certName in setting.trustedRootCertificateNames: {
        //   id: resourceId('Microsoft.Network/applicationGateways/trustedRootCertificates', appGatewayName, certName)
        // }] : []
        // authenticationCertificates: !empty(setting.?authenticationCertificateNames ?? []) ? [for certName in setting.authenticationCertificateNames: {
        //   id: resourceId('Microsoft.Network/applicationGateways/authenticationCertificates', appGatewayName, certName)
        // }] : []
      }
    }]
    // Health probes
    probes: [for probe in healthProbes: {
      name: probe.name
      properties: {
        protocol: probe.protocol
        host: probe.?host ?? ''
        path: probe.path
        interval: probe.?interval ?? 30
        timeout: probe.?timeout ?? 30
        unhealthyThreshold: probe.?unhealthyThreshold ?? 3
        pickHostNameFromBackendHttpSettings: probe.?pickHostNameFromBackendHttpSettings ?? false
        minServers: probe.?minServers ?? 0
        match: probe.?match ?? {
          statusCodes: ['200-399']
        }
      }
    }]
    // HTTP listeners
    httpListeners: [for listener in httpListeners: {
      name: listener.name
      properties: {
        frontendIPConfiguration: {
          id: resourceId('Microsoft.Network/applicationGateways/frontendIPConfigurations', appGatewayName, listener.frontendIPConfigurationName)
        }
        frontendPort: {
          id: resourceId('Microsoft.Network/applicationGateways/frontendPorts', appGatewayName, listener.frontendPortName)
        }
        protocol: listener.protocol
        hostName: listener.?hostName ?? ''
        hostNames: listener.?hostNames ?? []
        requireServerNameIndication: listener.?requireServerNameIndication ?? false
        sslCertificate: !empty(listener.?sslCertificateName ?? '') ? {
          id: resourceId('Microsoft.Network/applicationGateways/sslCertificates', appGatewayName, listener.sslCertificateName)
        } : null
        firewallPolicy: !empty(listener.?firewallPolicyId ?? '') ? {
          id: listener.firewallPolicyId
        } : null
        customErrorConfigurations: listener.?customErrorConfigurations ?? []
      }
    }]
    // URL path maps
    urlPathMaps: [for pathMap in urlPathMaps: {
      name: pathMap.name
      properties: {
        defaultBackendAddressPool: !empty(pathMap.?defaultBackendAddressPoolName ?? '') ? {
          id: resourceId('Microsoft.Network/applicationGateways/backendAddressPools', appGatewayName, pathMap.defaultBackendAddressPoolName)
        } : null
        defaultBackendHttpSettings: !empty(pathMap.?defaultBackendHttpSettingsName ?? '') ? {
          id: resourceId('Microsoft.Network/applicationGateways/backendHttpSettingsCollection', appGatewayName, pathMap.defaultBackendHttpSettingsName)
        } : null
        defaultRedirectConfiguration: !empty(pathMap.?defaultRedirectConfigurationName ?? '') ? {
          id: resourceId('Microsoft.Network/applicationGateways/redirectConfigurations', appGatewayName, pathMap.defaultRedirectConfigurationName)
        } : null
        pathRules: pathMap.?pathRules ?? []
        // pathRules: [for rule in pathMap.pathRules: {
        //   name: rule.name
        //   properties: {
        //     paths: rule.paths
        //     backendAddressPool: !empty(rule.?backendAddressPoolName ?? '') ? {
        //       id: resourceId('Microsoft.Network/applicationGateways/backendAddressPools', appGatewayName, rule.backendAddressPoolName)
        //     } : null
        //     backendHttpSettings: !empty(rule.?backendHttpSettingsName ?? '') ? {
        //       id: resourceId('Microsoft.Network/applicationGateways/backendHttpSettingsCollection', appGatewayName, rule.backendHttpSettingsName)
        //     } : null
        //     redirectConfiguration: !empty(rule.?redirectConfigurationName ?? '') ? {
        //       id: resourceId('Microsoft.Network/applicationGateways/redirectConfigurations', appGatewayName, rule.redirectConfigurationName)
        //     } : null
        //     rewriteRuleSet: !empty(rule.?rewriteRuleSetName ?? '') ? {
        //       id: resourceId('Microsoft.Network/applicationGateways/rewriteRuleSets', appGatewayName, rule.rewriteRuleSetName)
        //     } : null
        //   }
        // }]
      }
    }]
    // Request routing rules
    requestRoutingRules: [for rule in requestRoutingRules: {
      name: rule.name
      properties: {
        ruleType: rule.ruleType
        priority: rule.?priority ?? 100
        httpListener: {
          id: resourceId('Microsoft.Network/applicationGateways/httpListeners', appGatewayName, rule.httpListenerName)
        }
        backendAddressPool: rule.ruleType == 'Basic' && !empty(rule.?backendAddressPoolName ?? '') ? {
          id: resourceId('Microsoft.Network/applicationGateways/backendAddressPools', appGatewayName, rule.backendAddressPoolName)
        } : null
        backendHttpSettings: rule.ruleType == 'Basic' && !empty(rule.?backendHttpSettingsName ?? '') ? {
          id: resourceId('Microsoft.Network/applicationGateways/backendHttpSettingsCollection', appGatewayName, rule.backendHttpSettingsName)
        } : null
        urlPathMap: rule.ruleType == 'PathBasedRouting' && !empty(rule.?urlPathMapName ?? '') ? {
          id: resourceId('Microsoft.Network/applicationGateways/urlPathMaps', appGatewayName, rule.urlPathMapName)
        } : null
        redirectConfiguration: !empty(rule.?redirectConfigurationName ?? '') ? {
          id: resourceId('Microsoft.Network/applicationGateways/redirectConfigurations', appGatewayName, rule.redirectConfigurationName)
        } : null
        rewriteRuleSet: !empty(rule.?rewriteRuleSetName ?? '') ? {
          id: resourceId('Microsoft.Network/applicationGateways/rewriteRuleSets', appGatewayName, rule.rewriteRuleSetName)
        } : null
      }
    }]
    // SSL policy
    sslPolicy: sslPolicy
    // Web Application Firewall configuration
    webApplicationFirewallConfiguration: appGatewayTier == 'WAF_v2' ? {
      enabled: wafConfiguration.enabled
      firewallMode: wafConfiguration.firewallMode
      ruleSetType: wafConfiguration.ruleSetType
      ruleSetVersion: wafConfiguration.ruleSetVersion
      fileUploadLimitInMb: wafConfiguration.?fileUploadLimitInMb ?? 100
      requestBodyCheck: wafConfiguration.?requestBodyCheck ?? true
      maxRequestBodySizeInKb: wafConfiguration.?maxRequestBodySizeInKb ?? 128
      disabledRuleGroups: wafConfiguration.?disabledRuleGroups ?? []
      exclusions: wafConfiguration.?exclusions ?? []
    } : null
    // Additional settings
    enableHttp2: enableHttp2
    enableFips: enableFips
  }
}

// ==========================================
// OUTPUTS
// ==========================================

@description('Application Gateway resource ID')
output appGatewayId string = enableAppGateway ? appGateway.id : ''

@description('Application Gateway name')
output appGatewayName string = enableAppGateway ? appGateway.name : ''

@description('Application Gateway operational state')
output operationalState string = enableAppGateway ? appGateway.properties.?operationalState ?? 'Unknown' : 'Disabled'

@description('Application Gateway system assigned managed identity principal ID')
output systemAssignedIdentityPrincipalId string = enableAppGateway && enableSystemManagedIdentity ? appGateway.identity.?principalId ?? '' : ''

@description('Application Gateway configuration summary')
output configurationSummary object = enableAppGateway ? {
  sku: {
    name: appGateway.properties.sku.name
    tier: appGateway.properties.sku.tier
  }
  capacity: {
    min: appGateway.properties.?autoscaleConfiguration.?minCapacity ?? 0
    max: appGateway.properties.?autoscaleConfiguration.?maxCapacity ?? 0
  }
  wafEnabled: appGateway.properties.?webApplicationFirewallConfiguration.?enabled ?? false
  http2Enabled: appGateway.properties.?enableHttp2 ?? false
  backendPoolCount: length(backendPools)
  listenerCount: length(httpListeners)
  routingRuleCount: length(requestRoutingRules)
  zones: availabilityZones
} : {}

var backendPoolSummaryVar = [for (pool, i) in backendPools: {
  name: pool.name
  backendAddressCount: length(backendAddressesFlattened[i].addresses)
  fqdnCount: length(pool.?fqdns ?? [])
  ipAddressCount: length(pool.?ipAddresses ?? [])
}]

@description('Backend pool configuration summary')
var listenerSummaryVar = [for listener in httpListeners: {
  name: listener.name
  protocol: listener.protocol
  port: listener.?frontendPortName ?? ''
  sslEnabled: !empty(listener.?sslCertificateName ?? '')
}]

@description('HTTP listener summary')
var healthProbeSummaryVar = [for probe in healthProbes: {
  name: probe.name
  protocol: probe.protocol
  path: probe.path
  interval: probe.?interval ?? 30
  timeout: probe.?timeout ?? 30
}]

@description('Health probe summary')
var urlPathMapSummaryVar = [for pathMap in urlPathMaps: {
  name: pathMap.name
  pathRuleCount: length(pathMap.pathRules)
  defaultBackend: pathMap.?defaultBackendAddressPoolName ?? ''
}]

@description('URL path map summary')
output urlPathMapSummary array = enableAppGateway ? urlPathMapSummaryVar : []

@description('Backend pool summary')
output backendPoolSummary array = enableAppGateway ? backendPoolSummaryVar : []

@description('HTTP listener summary')
output httpListenerSummary array = enableAppGateway ? listenerSummaryVar : []

@description('Health probe summary')
output healthProbeSummary array = enableAppGateway ? healthProbeSummaryVar : []

