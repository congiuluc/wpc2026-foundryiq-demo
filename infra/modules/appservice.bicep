@description('Region of the App Service plan and web app.')
param location string

@description('Suffix used to make resource names unique.')
param resourceToken string

@description('App Service plan SKU. B1 is the cheapest tier with Always On; F1 is free but sleeps after inactivity and has a daily CPU quota.')
@allowed([
  'F1'
  'B1'
  'B2'
  'B3'
  'S1'
  'P0v3'
])
param skuName string = 'B1'

@description('azd service name, used by azd deploy to find the web app.')
param serviceName string = 'mcp-ricambi'

@description('Fully qualified name of the Azure SQL logical server.')
param sqlServerFqdn string

@description('Name of the spare parts database.')
param sqlDatabaseName string

@secure()
@description('API key that clients (Foundry IQ) must send in the x-api-key header.')
param mcpApiKey string

@description('Resource ID of the user-assigned identity used for SQL (private-endpoint mode). Empty = system-assigned identity.')
param sqlIdentityId string = ''

@description('Client ID of the user-assigned identity used for SQL.')
param sqlIdentityClientId string = ''

@description('Subnet for regional VNet integration (private-endpoint mode). Empty = no VNet integration.')
param vnetSubnetId string = ''

param tags object = {}

var appName = 'wpc-foundryiq-mcp-${resourceToken}'
var isFree = skuName == 'F1'
var useUserIdentity = !empty(sqlIdentityId)

resource plan 'Microsoft.Web/serverfarms@2024-04-01' = {
  name: 'wpc-foundryiq-plan-${resourceToken}'
  location: location
  tags: tags
  kind: 'linux'
  sku: {
    name: skuName
  }
  properties: {
    reserved: true
  }
}

resource app 'Microsoft.Web/sites@2024-04-01' = {
  name: appName
  location: location
  tags: union(tags, {
    'azd-service-name': serviceName
  })
  kind: 'app,linux'
  identity: useUserIdentity ? {
    type: 'SystemAssigned, UserAssigned'
    userAssignedIdentities: {
      '${sqlIdentityId}': {}
    }
  } : {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    virtualNetworkSubnetId: empty(vnetSubnetId) ? null : vnetSubnetId
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.12'
      alwaysOn: !isFree
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      http20Enabled: true
      healthCheckPath: '/healthz'
      appCommandLine: 'python -m uvicorn server:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips=*'
    }
  }

  // azd deploys with Microsoft Entra tokens, so basic publishing credentials stay disabled.
  resource ftp 'basicPublishingCredentialsPolicies' = {
    name: 'ftp'
    properties: {
      allow: false
    }
  }

  resource scm 'basicPublishingCredentialsPolicies' = {
    name: 'scm'
    properties: {
      allow: false
    }
  }
}

resource appSettings 'Microsoft.Web/sites/config@2024-04-01' = {
  parent: app
  name: 'appsettings'
  properties: union({
    SCM_DO_BUILD_DURING_DEPLOYMENT: 'true'
    ENABLE_ORYX_BUILD: 'true'
    WEBSITES_PORT: '8000'
    MCP_API_KEY: mcpApiKey
    MCP_ALLOWED_HOSTS: app.properties.defaultHostName
    INVENTORY_SOURCE: 'sql'
    INVENTORY_FALLBACK_TO_JSON: 'true'
    SQL_SERVER: sqlServerFqdn
    SQL_DATABASE: sqlDatabaseName
    SQL_AUTHENTICATION: 'ActiveDirectoryMSI'
  }, useUserIdentity ? {
    // The user-assigned identity is the SQL Entra admin: the server creates the schema and loads the catalog.
    SQL_CLIENT_ID: sqlIdentityClientId
    SQL_AUTO_SEED: 'true'
  } : {})
}

output name string = app.name
output planId string = plan.id
output uri string = 'https://${app.properties.defaultHostName}'
output principalId string = app.identity.principalId
