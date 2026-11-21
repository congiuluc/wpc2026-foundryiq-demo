@description('Region of the resources.')
param location string

@description('Suffix used to make resource names unique.')
param resourceToken string

@description('Container (ADLS Gen2 file system) holding the documents protected by POSIX ACLs.')
param containerName string = 'acl-docs'

param tags object = {}

// ADLS Gen2 (hierarchical namespace) is required to ingest per-document ACLs into Foundry IQ.
resource storage 'Microsoft.Storage/storageAccounts@2024-01-01' = {
  name: 'wpcfiqacl${resourceToken}'
  location: location
  kind: 'StorageV2'
  sku: {
    name: 'Standard_LRS'
  }
  tags: union(tags, {
    SecurityControl: 'Ignore'
  })
  properties: {
    isHnsEnabled: true
    publicNetworkAccess: 'Enabled'
    supportsHttpsTrafficOnly: true
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    defaultToOAuthAuthentication: true
  }
}

resource blobs 'Microsoft.Storage/storageAccounts/blobServices@2024-01-01' = {
  parent: storage
  name: 'default'
}

resource docs 'Microsoft.Storage/storageAccounts/blobServices/containers@2024-01-01' = {
  parent: blobs
  name: containerName
  properties: {
    publicAccess: 'None'
  }
}

output name string = storage.name
output id string = storage.id
output dfsEndpoint string = storage.properties.primaryEndpoints.dfs
output containerName string = docs.name
