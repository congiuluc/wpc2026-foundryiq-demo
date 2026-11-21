@description('Region of the resources.')
param location string

@description('Suffix used to make resource names unique.')
param resourceToken string

@description('Container that holds the machine specification documents.')
param containerName string = 'machinespecs'

param tags object = {}

resource storage 'Microsoft.Storage/storageAccounts@2024-01-01' = {
  name: 'wpcfiq${resourceToken}'
  location: location
  kind: 'StorageV2'
  sku: {
    name: 'Standard_LRS'
  }
  tags: union(tags, {
    SecurityControl: 'Ignore'
  })
  properties: {
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

resource specs 'Microsoft.Storage/storageAccounts/blobServices/containers@2024-01-01' = {
  parent: blobs
  name: containerName
  properties: {
    publicAccess: 'None'
  }
}

output name string = storage.name
output id string = storage.id
output blobEndpoint string = storage.properties.primaryEndpoints.blob
output containerName string = specs.name
