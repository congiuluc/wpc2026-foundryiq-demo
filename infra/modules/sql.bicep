@description('Region of the SQL logical server and database.')
param location string

@description('Suffix used to make resource names unique.')
param resourceToken string

@description('Display name of the Microsoft Entra administrator (UPN for users).')
param adminLogin string

@description('Object ID of the Microsoft Entra administrator.')
param adminObjectId string

@allowed([
  'User'
  'Group'
  'Application'
])
param adminPrincipalType string = 'User'

@description('Name of the spare parts database.')
param databaseName string = 'ricambi'

@description('Use the Azure SQL Database free offer (max 10 free databases per subscription). Set to false when the free slots are exhausted.')
param useFreeLimit bool = true

@description('Disable public network access and skip the firewall rule (the server is reached through a private endpoint).')
param privateOnly bool = false

param tags object = {}

resource sqlServer 'Microsoft.Sql/servers@2024-05-01-preview' = {
  name: 'wpc-foundryiq-sql-${resourceToken}'
  location: location
  tags: tags
  properties: {
    minimalTlsVersion: '1.2'
    publicNetworkAccess: privateOnly ? 'Disabled' : 'Enabled'
    administrators: {
      administratorType: 'ActiveDirectory'
      azureADOnlyAuthentication: true
      login: adminLogin
      sid: adminObjectId
      principalType: adminPrincipalType
      tenantId: tenant().tenantId
    }
  }
}

// The inline administrators block only applies at server creation; this child resource also updates an existing server.
resource entraAdmin 'Microsoft.Sql/servers/administrators@2024-05-01-preview' = {
  parent: sqlServer
  name: 'ActiveDirectory'
  properties: {
    administratorType: 'ActiveDirectory'
    login: adminLogin
    sid: adminObjectId
    tenantId: tenant().tenantId
  }
}

// Lets App Service (and other Azure services) reach the server; access still requires Entra authentication.
// The 0.0.0.0-0.0.0.0 range means "Azure services"; the name avoids the reserved word flagged by azd preflight.
resource allowAzureServices 'Microsoft.Sql/servers/firewallRules@2024-05-01-preview' = if (!privateOnly) {
  parent: sqlServer
  name: 'AllowAzureServices'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
}

// Serverless General Purpose is the SKU required by the free offer (100k vCore seconds and 32 GB per month).
resource database 'Microsoft.Sql/servers/databases@2024-05-01-preview' = {
  parent: sqlServer
  name: databaseName
  location: location
  tags: tags
  sku: {
    name: 'GP_S_Gen5'
    tier: 'GeneralPurpose'
    family: 'Gen5'
    capacity: 2
  }
  properties: {
    collation: 'SQL_Latin1_General_CP1_CI_AS'
    maxSizeBytes: 34359738368
    autoPauseDelay: 60
    minCapacity: json('0.5')
    zoneRedundant: false
    requestedBackupStorageRedundancy: 'Local'
    useFreeLimit: useFreeLimit
    freeLimitExhaustionBehavior: useFreeLimit ? 'AutoPause' : null
  }
}

output serverName string = sqlServer.name
output serverId string = sqlServer.id
output serverFqdn string = sqlServer.properties.fullyQualifiedDomainName
output databaseName string = database.name
