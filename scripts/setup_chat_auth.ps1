#!/usr/bin/env pwsh
# Configures Entra sign-in (App Service Easy Auth) for the chat web app.
#  - app registration wpc-foundryiq-chat-<env> with redirect URI and delegated permission on Azure AI Search
#  - federated credential with the user-assigned managed identity of the web app (no client secret)
#  - authsettingsV2 on the site: login required, token store, access token for https://search.azure.com
#  - admin consent attempt (if it fails, every user consents at first login)
#  - saves CHAT_APP_CLIENT_ID in the azd env, so later provisions apply the same configuration from Bicep
# Until this script succeeds, Bicep keeps authentication required with no identity provider (HTTP 401).
# Idempotent: it can be run again with pwsh ./scripts/setup_chat_auth.ps1
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot 'env_settings.ps1')

if (-not $env:CHAT_APP_NAME) {
    # Running outside the hooks: load azd env values or Bicep outputs + local state (scripts/load_env.ps1).
    & (Join-Path $PSScriptRoot 'load_env.ps1') -Quiet
}
foreach ($name in 'AZURE_RESOURCE_GROUP', 'AZURE_SUBSCRIPTION_ID', 'CHAT_APP_NAME', 'CHAT_APP_URL', 'CHAT_APP_AUTH_IDENTITY_PRINCIPAL_ID') {
    if (-not [Environment]::GetEnvironmentVariable($name)) { throw "Variabile $name mancante: eseguire prima la fase provision (azd provision o deploy_noazd.ps1)." }
}

function Invoke-Az([string]$What, [string[]]$Arguments) {
    $output = & az @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$What fallito (exit code $LASTEXITCODE)" }
    return $output
}

function Write-TempJson($Object) {
    $path = [IO.Path]::GetTempFileName()
    $Object | ConvertTo-Json -Depth 20 | Set-Content -Path $path -Encoding utf8
    return $path
}

$graphAppId = '00000003-0000-0000-c000-000000000000'
$graphUserRead = 'e1fe6dd8-ba31-4d61-89e7-88639da4683d'
$searchAppId = '880da380-985e-4198-81b9-e05b1cc53158'
$tenantId = Invoke-Az 'Lettura tenant' @('account', 'show', '--query', 'tenantId', '-o', 'tsv')
$envName = if ($env:AZURE_ENV_NAME) { $env:AZURE_ENV_NAME } else { 'dev' }
$displayName = "wpc-foundryiq-chat-$envName"
$redirectUri = "$($env:CHAT_APP_URL.TrimEnd('/'))/.auth/login/aad/callback"
$tempFiles = @()

try {
    Write-Host "App registration '$displayName'..."
    $appId = Invoke-Az 'Ricerca app registration' @('ad', 'app', 'list', '--display-name', $displayName, '--query', '[0].appId', '-o', 'tsv')
    if (-not $appId) {
        $appId = Invoke-Az 'Creazione app registration' @('ad', 'app', 'create', '--display-name', $displayName,
            '--sign-in-audience', 'AzureADMyOrg', '--web-redirect-uris', $redirectUri,
            '--enable-id-token-issuance', 'true', '--query', 'appId', '-o', 'tsv')
        Write-Host "  creata (appId $appId)."
    }
    else {
        Invoke-Az 'Aggiornamento redirect URI' @('ad', 'app', 'update', '--id', $appId, '--web-redirect-uris', $redirectUri,
            '--enable-id-token-issuance', 'true') | Out-Null
        Write-Host "  esistente (appId $appId), redirect URI aggiornato."
    }

    $access = @(@{ resourceAppId = $graphAppId; resourceAccess = @(@{ id = $graphUserRead; type = 'Scope' }) })
    $searchScopeId = az ad sp show --id $searchAppId --query "oauth2PermissionScopes[?value=='user_impersonation'].id | [0]" -o tsv 2>$null
    if ($LASTEXITCODE -eq 0 -and $searchScopeId) {
        $access += @{ resourceAppId = $searchAppId; resourceAccess = @(@{ id = $searchScopeId; type = 'Scope' }) }
    }
    else {
        Write-Warning 'Service principal di Azure AI Search non trovato nel tenant: il permesso user_impersonation sara richiesto al primo login.'
    }
    $accessFile = Write-TempJson $access
    $tempFiles += $accessFile
    Invoke-Az 'Permessi delegati' @('ad', 'app', 'update', '--id', $appId, '--required-resource-accesses', "@$accessFile") | Out-Null
    # Groups in the app's ID token are display-only; the Search access token remains unchanged.
    Invoke-Az 'Claim dei gruppi di sicurezza' @('ad', 'app', 'update', '--id', $appId, '--set', 'groupMembershipClaims=SecurityGroup') | Out-Null

    az ad sp show --id $appId --query id -o tsv 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Invoke-Az 'Creazione service principal' @('ad', 'sp', 'create', '--id', $appId) | Out-Null
    }

    Write-Host 'Federated credential con la managed identity della web app...'
    $ficName = 'webapp-chat-managed-identity'
    $existing = Invoke-Az 'Lettura federated credential' @('ad', 'app', 'federated-credential', 'list', '--id', $appId, '--query', "[?name=='$ficName'].name | [0]", '-o', 'tsv')
    if (-not $existing) {
        $ficFile = Write-TempJson @{
            name      = $ficName
            issuer    = "https://login.microsoftonline.com/$tenantId/v2.0"
            subject   = $env:CHAT_APP_AUTH_IDENTITY_PRINCIPAL_ID
            audiences = @('api://AzureADTokenExchange')
        }
        $tempFiles += $ficFile
        Invoke-Az 'Creazione federated credential' @('ad', 'app', 'federated-credential', 'create', '--id', $appId, '--parameters', "@$ficFile") | Out-Null
    }

    Write-Host "Autenticazione App Service su '$env:CHAT_APP_NAME'..."
    $authFile = Write-TempJson @{
        properties = @{
            platform          = @{ enabled = $true; runtimeVersion = '~1' }
            globalValidation  = @{
                requireAuthentication       = $true
                unauthenticatedClientAction = 'RedirectToLoginPage'
                redirectToProvider          = 'azureactivedirectory'
                excludedPaths               = @('/healthz')
            }
            identityProviders = @{
                azureActiveDirectory = @{
                    enabled      = $true
                    registration = @{
                        openIdIssuer            = "https://login.microsoftonline.com/$tenantId/v2.0"
                        clientId                = $appId
                        # App setting with the managed identity client ID used as client assertion.
                        clientSecretSettingName = 'OVERRIDE_USE_MI_FIC_ASSERTION_CLIENTID'
                    }
                    login        = @{
                        # The access token stored by Easy Auth is issued for Azure AI Search.
                        loginParameters = @('scope=openid profile email offline_access https://search.azure.com/user_impersonation')
                    }
                    validation   = @{ allowedAudiences = @($appId, "api://$appId") }
                }
            }
            login             = @{ tokenStore = @{ enabled = $true } }
            httpSettings      = @{ requireHttps = $true }
        }
    }
    $tempFiles += $authFile
    $uri = "https://management.azure.com/subscriptions/$env:AZURE_SUBSCRIPTION_ID/resourceGroups/$env:AZURE_RESOURCE_GROUP/providers/Microsoft.Web/sites/$env:CHAT_APP_NAME/config/authsettingsV2?api-version=2024-04-01"
    Invoke-Az 'Configurazione authsettingsV2' @('rest', '--method', 'put', '--uri', $uri, '--body', "@$authFile") | Out-Null

    az ad app permission admin-consent --id $appId 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Warning 'Admin consent non concesso (servono privilegi di amministratore): ogni utente confermera i permessi al primo accesso.'
    }

    Save-EnvSetting 'CHAT_APP_CLIENT_ID' $appId
    Write-Host "Chat web app protetta da Entra ID: $env:CHAT_APP_URL" -ForegroundColor Green
}
finally {
    $tempFiles | ForEach-Object { Remove-Item $_ -ErrorAction SilentlyContinue }
}
