@description('Principal ID of the chat web app system-assigned identity.')
param principalId string

param searchServiceName string
param foundryAccountName string

var searchIndexDataReader = '1407120a-92aa-4202-b7e9-c0e197c71c8f'
var openAIUser = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
var azureAIUser = '53ca6127-db72-4b80-b1b0-d745d6d5456d'

resource search 'Microsoft.Search/searchServices@2025-05-01' existing = {
  name: searchServiceName
}

resource foundry 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: foundryAccountName
}

// Authorizes the knowledge base retrieve calls. Document access is still evaluated on the user token.
resource searchRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: search
  name: guid(search.id, principalId, searchIndexDataReader)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', searchIndexDataReader)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}

// Answer synthesis with the chat model deployment.
resource openAIRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: foundry
  name: guid(foundry.id, principalId, openAIUser)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', openAIUser)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}

// Invokes the Foundry agents (Responses API with agent_reference) in the "Agenti" comparison mode.
resource agentsRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: foundry
  name: guid(foundry.id, principalId, azureAIUser)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', azureAIUser)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}
