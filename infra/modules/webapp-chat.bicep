@description('Region of the chat web app.')
param location string

@description('Suffix used to make resource names unique.')
param resourceToken string

@description('Resource ID of the App Service plan shared with the MCP server.')
param planId string

@description('Always On is not available on the F1 (free) plan.')
param alwaysOn bool = true

@description('azd service name, used by azd deploy to find the web app.')
param serviceName string = 'webapp-chat'

param searchEndpoint string
param openAIEndpoint string
param chatDeploymentName string
@description('Foundry project endpoint, used to invoke the agents compared in the "Agenti" mode.')
param projectEndpoint string
param demoKnowledgeBaseName string = 'wpc-foundryiq-kb'
param aclKnowledgeBaseName string = 'wpc-acl-kb'

@description('Client ID of the sign-in app registration created by scripts/setup_chat_auth.ps1. Empty: every request is rejected with 401.')
param authClientId string = ''

param tags object = {}

// Used only as a federated credential of the sign-in app registration (no client secret).
// Data-plane calls use the system-assigned identity.
resource authIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-wpc-foundryiq-chat-auth-${resourceToken}'
  location: location
  tags: tags
}

resource app 'Microsoft.Web/sites@2024-04-01' = {
  name: 'wpc-foundryiq-chat-${resourceToken}'
  location: location
  tags: union(tags, {
    'azd-service-name': serviceName
  })
  kind: 'app,linux'
  identity: {
    type: 'SystemAssigned, UserAssigned'
    userAssignedIdentities: {
      '${authIdentity.id}': {}
    }
  }
  properties: {
    serverFarmId: planId
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.12'
      alwaysOn: alwaysOn
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      http20Enabled: true
      healthCheckPath: '/healthz'
      appCommandLine: 'python -m uvicorn app:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips=*'
    }
  }

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
  properties: {
    SCM_DO_BUILD_DURING_DEPLOYMENT: 'true'
    ENABLE_ORYX_BUILD: 'true'
    WEBSITES_PORT: '8000'
    FOUNDRYIQ_DEMO_SEARCH_ENDPOINT: searchEndpoint
    FOUNDRYIQ_DEMO_OPENAI_ENDPOINT: openAIEndpoint
    FOUNDRYIQ_DEMO_CHAT_DEPLOYMENT: chatDeploymentName
    FOUNDRYIQ_DEMO_PROJECT_ENDPOINT: projectEndpoint
    FOUNDRYIQ_DEMO_KB_NAME: demoKnowledgeBaseName
    FOUNDRYIQ_ACL_KB_NAME: aclKnowledgeBaseName
    // App Service authentication uses this identity as client assertion instead of a secret.
    OVERRIDE_USE_MI_FIC_ASSERTION_CLIENTID: authIdentity.properties.clientId
  }
}

// Fail-closed: authentication is always required. Until the app registration exists there is no
// identity provider, so App Service answers 401 to every request except the health check.
var openIdIssuer = '${environment().authentication.loginEndpoint}${subscription().tenantId}/v2.0'
var entraProvider = empty(authClientId) ? {} : {
  azureActiveDirectory: {
    enabled: true
    registration: {
      openIdIssuer: openIdIssuer
      clientId: authClientId
      clientSecretSettingName: 'OVERRIDE_USE_MI_FIC_ASSERTION_CLIENTID'
    }
    login: {
      // The access token stored by Easy Auth is issued for Azure AI Search.
      loginParameters: [
        'scope=openid profile email offline_access https://search.azure.com/user_impersonation'
      ]
    }
    validation: {
      allowedAudiences: [
        authClientId
        'api://${authClientId}'
      ]
    }
  }
}

resource auth 'Microsoft.Web/sites/config@2024-04-01' = {
  parent: app
  name: 'authsettingsV2'
  dependsOn: [
    appSettings
  ]
  properties: {
    platform: {
      enabled: true
      runtimeVersion: '~1'
    }
    globalValidation: {
      requireAuthentication: true
      unauthenticatedClientAction: empty(authClientId) ? 'Return401' : 'RedirectToLoginPage'
      redirectToProvider: empty(authClientId) ? null : 'azureactivedirectory'
      excludedPaths: [
        '/healthz'
      ]
    }
    identityProviders: entraProvider
    login: {
      tokenStore: {
        enabled: true
      }
    }
    httpSettings: {
      requireHttps: true
    }
  }
}

output name string = app.name
output uri string = 'https://${app.properties.defaultHostName}'
output principalId string = app.identity.principalId
output authIdentityPrincipalId string = authIdentity.properties.principalId
