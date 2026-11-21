@description('Region of the virtual network (same as the App Service plan).')
param location string

@description('Suffix used to make resource names unique.')
param resourceToken string

@description('Resource ID of the Azure SQL logical server reached through the private endpoint.')
param sqlServerId string

param tags object = {}

// Private connectivity for Azure SQL: App Service VNet integration -> private endpoint -> SQL server.
// Required when a policy forces publicNetworkAccess = Disabled on SQL servers.
resource vnet 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: 'wpc-foundryiq-vnet-${resourceToken}'
  location: location
  tags: tags
  properties: {
    addressSpace: {
      addressPrefixes: [
        '10.60.0.0/16'
      ]
    }
    subnets: [
      {
        name: 'appservice'
        properties: {
          addressPrefix: '10.60.1.0/24'
          delegations: [
            {
              name: 'appservice'
              properties: {
                serviceName: 'Microsoft.Web/serverFarms'
              }
            }
          ]
        }
      }
      {
        name: 'private-endpoints'
        properties: {
          addressPrefix: '10.60.2.0/24'
        }
      }
    ]
  }
}

resource sqlDnsZone 'Microsoft.Network/privateDnsZones@2024-06-01' = {
  name: 'privatelink${environment().suffixes.sqlServerHostname}'
  location: 'global'
  tags: tags
}

resource sqlDnsLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = {
  parent: sqlDnsZone
  name: vnet.name
  location: 'global'
  tags: tags
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: vnet.id
    }
  }
}

resource sqlEndpoint 'Microsoft.Network/privateEndpoints@2024-05-01' = {
  name: 'wpc-foundryiq-sql-pe-${resourceToken}'
  location: location
  tags: tags
  properties: {
    subnet: {
      id: vnet.properties.subnets[1].id
    }
    privateLinkServiceConnections: [
      {
        name: 'sql'
        properties: {
          privateLinkServiceId: sqlServerId
          groupIds: [
            'sqlServer'
          ]
        }
      }
    ]
  }

  resource dnsGroup 'privateDnsZoneGroups' = {
    name: 'default'
    properties: {
      privateDnsZoneConfigs: [
        {
          name: 'sql'
          properties: {
            privateDnsZoneId: sqlDnsZone.id
          }
        }
      ]
    }
  }
}

output appServiceSubnetId string = vnet.properties.subnets[0].id
output vnetName string = vnet.name
