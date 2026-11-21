@description('Object ID of the interactive demo user.')
param demoUserObjectId string

@allowed([
  'User'
  'Group'
  'ServicePrincipal'
])
param demoUserPrincipalType string = 'User'

param searchServiceName string
param storageAccountName string
param containerName string
@description('ADLS Gen2 account used by the ACL demo.')
param aclStorageAccountName string
param foundryAccountName string
param foundryProjectName string
param applicationInsightsName string

var searchRoles = [
  '7ca78c08-252a-4471-8644-bb5ff32d4ba0' // Search Service Contributor
  '8ebe5a00-799e-43f5-93ac-243d3dce84a7' // Search Index Data Contributor
]
var demoFoundryRoles = [
  '53ca6127-db72-4b80-b1b0-d745d6d5456d' // Foundry User
  'a97b65f3-24c7-4388-baec-2e87135dc908' // Cognitive Services User
  'eadc314b-1a2d-4efa-be10-5d325db5065e' // Foundry Project Manager
]
var blobReader = '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'
var blobContributor = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
var blobOwner = 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b'
var openAIUser = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
var searchReader = '1407120a-92aa-4202-b7e9-c0e197c71c8f'
var logAnalyticsReader = '73c42c96-874c-492b-b04d-ab87d138a893'

resource search 'Microsoft.Search/searchServices@2025-05-01' existing = {
  name: searchServiceName
}

resource storage 'Microsoft.Storage/storageAccounts@2024-01-01' existing = {
  name: storageAccountName

  resource blobs 'blobServices' existing = {
    name: 'default'

    resource specs 'containers' existing = {
      name: containerName
    }
  }
}

resource aclStorage 'Microsoft.Storage/storageAccounts@2024-01-01' existing = {
  name: aclStorageAccountName
}

resource foundry 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: foundryAccountName

  resource project 'projects' existing = {
    name: foundryProjectName
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: applicationInsightsName
}

// Demo user

resource demoAppInsightsRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: appInsights
  name: guid(appInsights.id, demoUserObjectId, logAnalyticsReader)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', logAnalyticsReader)
    principalId: demoUserObjectId
    principalType: demoUserPrincipalType
  }
}

resource demoFoundryRoleAssignments 'Microsoft.Authorization/roleAssignments@2022-04-01' = [
  for role in demoFoundryRoles: {
    scope: foundry
    name: guid(foundry.id, demoUserObjectId, role)
    properties: {
      roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', role)
      principalId: demoUserObjectId
      principalType: demoUserPrincipalType
    }
  }
]

resource demoSearchRoles 'Microsoft.Authorization/roleAssignments@2022-04-01' = [
  for role in searchRoles: {
    scope: search
    name: guid(search.id, demoUserObjectId, role)
    properties: {
      roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', role)
      principalId: demoUserObjectId
      principalType: demoUserPrincipalType
    }
  }
]

resource demoBlobRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: storage::blobs::specs
  name: guid(storage::blobs::specs.id, demoUserObjectId, blobContributor)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', blobContributor)
    principalId: demoUserObjectId
    principalType: demoUserPrincipalType
  }
}

// Data Owner is needed to upload the ACL demo documents and set their POSIX ACLs. It does not bypass
// Foundry IQ trimming because the knowledge source ingests only userIds/groupIds (no rbacScope).
resource demoAclStorageRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: aclStorage
  name: guid(aclStorage.id, demoUserObjectId, blobOwner)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', blobOwner)
    principalId: demoUserObjectId
    principalType: demoUserPrincipalType
  }
}

// Foundry project managed identity

resource projectSearchRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: search
  name: guid(search.id, foundry::project.id, searchReader)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', searchReader)
    principalId: foundry::project.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource projectModelRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: foundry
  name: guid(foundry.id, foundry::project.id, openAIUser)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', openAIUser)
    principalId: foundry::project.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// Search managed identity

resource searchBlobRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: storage
  name: guid(storage.id, search.id, blobReader)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', blobReader)
    principalId: search.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource searchAclBlobRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: aclStorage
  name: guid(aclStorage.id, search.id, blobReader)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', blobReader)
    principalId: search.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource searchModelRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: foundry
  name: guid(foundry.id, search.id, openAIUser)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', openAIUser)
    principalId: search.identity.principalId
    principalType: 'ServicePrincipal'
  }
}
