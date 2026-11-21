@description('Region that supports Foundry IQ and all model deployments.')
param location string

@description('Suffix used to make resource names unique.')
param resourceToken string

@description('Name of the project created inside the Foundry account.')
param projectName string

@description('Azure OpenAI chat model supported by Foundry IQ.')
param chatModel string

@description('Version of the chat model available in the target region.')
param chatModelVersion string

@description('SKU for the chat model deployment.')
param chatDeploymentSku string

@minValue(1)
param chatCapacity int

@minValue(1)
param embeddingCapacity int

@minValue(1)
param gpt52Capacity int

@description('Name of the Azure AI Search service connected to the project.')
param searchServiceName string

@description('Name of the Application Insights component connected to the project.')
param applicationInsightsName string

param tags object = {}

var foundryName = 'wpc-foundryiq-ai-${resourceToken}'
var embeddingModel = 'text-embedding-3-large'
var gpt52Model = 'gpt-5.2'

resource search 'Microsoft.Search/searchServices@2025-05-01' existing = {
  name: searchServiceName
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: applicationInsightsName
}

resource foundry 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: foundryName
  location: location
  kind: 'AIServices'
  identity: {
    type: 'SystemAssigned'
  }
  sku: {
    name: 'S0'
  }
  tags: tags
  properties: {
    allowProjectManagement: true
    customSubDomainName: foundryName
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
  }
}

resource chat 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: foundry
  name: chatModel
  sku: {
    name: chatDeploymentSku
    capacity: chatCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: chatModel
      version: chatModelVersion
    }
  }
}

resource embedding 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: foundry
  name: embeddingModel
  sku: {
    name: 'Standard'
    capacity: embeddingCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: embeddingModel
      version: '1'
    }
  }
  // Serialize deployments to avoid concurrent writes to the same account.
  dependsOn: [chat]
}

resource gpt52 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: foundry
  name: gpt52Model
  sku: {
    name: 'GlobalStandard'
    capacity: gpt52Capacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: gpt52Model
      version: '2025-12-11'
    }
  }
  dependsOn: [embedding]
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = {
  parent: foundry
  name: projectName
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    displayName: 'WPC Foundry IQ Demo'
    description: 'Foundry project for the Zava knowledge retrieval demo.'
  }
  // Project and model writes share the account provisioning lock.
  dependsOn: [gpt52]
}

resource projectSearchConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-06-01' = {
  parent: project
  name: 'demo-search'
  properties: {
    category: 'CognitiveSearch'
    authType: 'AAD'
    target: 'https://${search.name}.search.windows.net'
    metadata: {
      ApiType: 'Azure'
      ResourceId: search.id
    }
  }
}

resource projectAppInsightsConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-06-01' = {
  parent: project
  name: 'demo-app-insights'
  properties: {
    category: 'AppInsights'
    authType: 'ApiKey'
    target: appInsights.id
    metadata: {
      ApiType: 'Azure'
      ResourceId: appInsights.id
    }
    credentials: {
      key: appInsights.properties.ConnectionString
    }
  }
  dependsOn: [projectSearchConnection]
}

output accountName string = foundry.name
output accountId string = foundry.id
output openAIEndpoint string = 'https://${foundry.name}.openai.azure.com'
output projectName string = project.name
output projectId string = project.id
output projectPrincipalId string = project.identity.principalId
output projectEndpoint string = 'https://${foundry.name}.services.ai.azure.com/api/projects/${project.name}'
output searchConnectionId string = projectSearchConnection.id
output chatDeploymentName string = chat.name
output chatModelName string = chatModel
output embeddingDeploymentName string = embedding.name
output gpt52DeploymentName string = gpt52.name
