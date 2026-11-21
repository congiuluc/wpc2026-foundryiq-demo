@description('Region that supports Foundry IQ (agentic retrieval).')
param location string

@description('Suffix used to make resource names unique.')
param resourceToken string

param tags object = {}

resource search 'Microsoft.Search/searchServices@2025-05-01' = {
  name: 'wpc-foundryiq-search-${resourceToken}'
  location: location
  tags: tags
  sku: {
    name: 'standard'
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    replicaCount: 1
    partitionCount: 1
    semanticSearch: 'standard'
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
  }
}

output name string = search.name
output id string = search.id
output endpoint string = 'https://${search.name}.search.windows.net'
output principalId string = search.identity.principalId
