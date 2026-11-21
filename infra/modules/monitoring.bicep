@description('Region of the resources.')
param location string

@description('Suffix used to make resource names unique.')
param resourceToken string

param tags object = {}

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'wpc-foundryiq-logs-${resourceToken}'
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
    features: {
      enableLogAccessUsingOnlyResourcePermissions: true
    }
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: 'wpc-foundryiq-insights-${resourceToken}'
  location: location
  kind: 'web'
  tags: tags
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalytics.id
    IngestionMode: 'LogAnalytics'
    RetentionInDays: 30
  }
}

output logAnalyticsWorkspaceId string = logAnalytics.id
output applicationInsightsName string = appInsights.name
output applicationInsightsId string = appInsights.id
