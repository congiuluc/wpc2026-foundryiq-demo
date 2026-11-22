// Exposes the demo configuration as a resource-group deployment with a stable name, so the notebook
// can keep reading outputs with `az deployment group show --name wpc-foundry-iq-demo`.

param demoConfig object
param foundryAccountName string
param foundryProjectName string
param foundryProjectId string
param foundryProjectEndpoint string
param storageAccountName string
param applicationInsightsName string
param applicationInsightsResourceId string
param logAnalyticsWorkspaceResourceId string

output demoConfig object = demoConfig
output storageAccountName string = storageAccountName
output foundryAccountName string = foundryAccountName
output foundryProjectName string = foundryProjectName
output foundryProjectId string = foundryProjectId
output foundryProjectEndpoint string = foundryProjectEndpoint
output applicationInsightsName string = applicationInsightsName
output applicationInsightsResourceId string = applicationInsightsResourceId
output logAnalyticsWorkspaceResourceId string = logAnalyticsWorkspaceResourceId
