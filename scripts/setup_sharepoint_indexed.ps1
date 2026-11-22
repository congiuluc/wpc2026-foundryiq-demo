#!/usr/bin/env pwsh
# Prepares the ingestion app for the indexedSharePoint knowledge source (policies-sharepoint-indexed-ks), secretless:
#  - app registration wpc-foundryiq-sp-ingestion-<env> with Microsoft Graph application permissions
#    Files.Read.All + Sites.FullControl.All (required to import user/group ACLs)
#  - admin consent (app role assignments): needs Global Admin / Privileged Role Admin, otherwise the script fails
#  - federated credential trusting the system-assigned managed identity of Azure AI Search (no client secret)
#  - saves SHAREPOINT_INDEXED_CONNECTION_STRING, used by scripts/setup_foundry_iq.ps1 (postdeploy)
# Requires SHAREPOINT_SITE_URL (scripts/setup_sharepoint.ps1). A connection string set by hand is left untouched.
# Idempotent: it can be run again with pwsh ./scripts/setup_sharepoint_indexed.ps1
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot 'env_settings.ps1')

if (-not $env:SEARCH_SERVICE_NAME) {
    # Running outside the hooks: load azd env values or Bicep outputs + local state (scripts/load_env.ps1).
    & (Join-Path $PSScriptRoot 'load_env.ps1') -Quiet
}
foreach ($name in 'AZURE_RESOURCE_GROUP', 'SEARCH_SERVICE_NAME') {
    if (-not [Environment]::GetEnvironmentVariable($name)) { throw "Variabile $name mancante: eseguire prima la fase provision (azd provision o deploy_noazd.ps1)." }
}
$siteUrl = Get-EnvSetting 'SHAREPOINT_SITE_URL'
if (-not $siteUrl) { throw 'SHAREPOINT_SITE_URL assente: eseguire prima scripts/setup_sharepoint.ps1.' }

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
$graphRoles = [ordered]@{
    'Files.Read.All'        = '01d4889c-1287-42c6-ac1f-5d1e02578ef6'
    'Sites.FullControl.All' = 'a82116e5-55eb-4c41-a434-62fe8a61c773'
}
$tenantId = Invoke-Az 'Lettura tenant' @('account', 'show', '--query', 'tenantId', '-o', 'tsv')
$envName = if ($env:AZURE_ENV_NAME) { $env:AZURE_ENV_NAME } else { 'dev' }
$displayName = "wpc-foundryiq-sp-ingestion-$envName"
$tempFiles = @()

$searchPrincipalId = Get-EnvSetting 'SEARCH_PRINCIPAL_ID'
if (-not $searchPrincipalId) {
    $searchPrincipalId = Invoke-Az 'Lettura identita Search' @('search', 'service', 'show', '-g', $env:AZURE_RESOURCE_GROUP,
        '-n', $env:SEARCH_SERVICE_NAME, '--query', 'identity.principalId', '-o', 'tsv')
}
if (-not $searchPrincipalId) { throw "Il servizio Search '$env:SEARCH_SERVICE_NAME' non ha un'identita gestita system-assigned." }
$searchClientId = Invoke-Az 'Lettura client ID identita Search' @('ad', 'sp', 'show', '--id', $searchPrincipalId, '--query', 'appId', '-o', 'tsv')

$existingConnection = Get-EnvSetting 'SHAREPOINT_INDEXED_CONNECTION_STRING'
if ($existingConnection -and $existingConnection -notmatch "FederatedCredentialApplicationId=$searchClientId(;|$)") {
    Write-Host '  SHAREPOINT_INDEXED_CONNECTION_STRING gia impostata manualmente: lasciata invariata.'
    return
}

try {
    Write-Host "App registration di ingestion '$displayName'..."
    $appId = Invoke-Az 'Ricerca app registration' @('ad', 'app', 'list', '--display-name', $displayName, '--query', '[0].appId', '-o', 'tsv')
    if (-not $appId) {
        $appId = Invoke-Az 'Creazione app registration' @('ad', 'app', 'create', '--display-name', $displayName,
            '--sign-in-audience', 'AzureADMyOrg', '--query', 'appId', '-o', 'tsv')
        Write-Host "  creata (appId $appId)."
    }
    else { Write-Host "  esistente (appId $appId)." }

    $accessFile = Write-TempJson @(@{
            resourceAppId  = $graphAppId
            resourceAccess = @($graphRoles.Values | ForEach-Object { @{ id = $_; type = 'Role' } })
        })
    $tempFiles += $accessFile
    Invoke-Az 'Permessi applicativi Graph' @('ad', 'app', 'update', '--id', $appId, '--required-resource-accesses', "@$accessFile") | Out-Null

    $spId = az ad sp show --id $appId --query id -o tsv 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $spId) {
        $spId = Invoke-Az 'Creazione service principal' @('ad', 'sp', 'create', '--id', $appId, '--query', 'id', '-o', 'tsv')
    }

    Write-Host 'Consenso amministrativo (Files.Read.All, Sites.FullControl.All)...'
    $graphSpId = Invoke-Az 'Lettura service principal Graph' @('ad', 'sp', 'show', '--id', $graphAppId, '--query', 'id', '-o', 'tsv')
    $assignmentsUri = "https://graph.microsoft.com/v1.0/servicePrincipals/$spId/appRoleAssignments"
    # A just-created service principal may not be visible to Graph yet.
    $granted = @()
    $readOk = $false
    for ($i = 1; $i -le 6 -and -not $readOk; $i++) {
        $json = az rest --method get --uri $assignmentsUri --query "value[?resourceId=='$graphSpId'].appRoleId" -o json 2>$null
        if ($LASTEXITCODE -eq 0) { $granted = $json | Out-String | ConvertFrom-Json -NoEnumerate; $readOk = $true }
        else { Start-Sleep -Seconds 10 }
    }
    if (-not $readOk) { throw 'Impossibile leggere le assegnazioni di ruolo del service principal di ingestion.' }
    foreach ($role in $graphRoles.GetEnumerator()) {
        if ($granted -contains $role.Value) { continue }
        $bodyFile = Write-TempJson @{ principalId = $spId; resourceId = $graphSpId; appRoleId = $role.Value }
        $tempFiles += $bodyFile
        az rest --method post --uri $assignmentsUri --body "@$bodyFile" --headers 'Content-Type=application/json' -o none 2>$null
        if ($LASTEXITCODE -ne 0) {
            throw "Consenso per $($role.Key) non concesso: servono Global Administrator o Privileged Role Administrator. Un amministratore puo concederlo dal portale Entra (App registrations > $displayName > API permissions > Grant admin consent), poi rilancia pwsh ./scripts/setup_sharepoint_indexed.ps1."
        }
        Write-Host "  $($role.Key) concesso."
    }

    Write-Host 'Federated credential con la managed identity di Search...'
    $ficName = 'search-managed-identity'
    $existing = Invoke-Az 'Lettura federated credential' @('ad', 'app', 'federated-credential', 'list', '--id', $appId, '--query', "[?name=='$ficName'].subject | [0]", '-o', 'tsv')
    if ($existing -and $existing -ne $searchPrincipalId) {
        # Search service recreated: the credential must trust the new identity.
        Invoke-Az 'Rimozione federated credential obsoleta' @('ad', 'app', 'federated-credential', 'delete', '--id', $appId, '--federated-credential-id', $ficName) | Out-Null
        $existing = $null
    }
    if (-not $existing) {
        $ficFile = Write-TempJson @{
            name      = $ficName
            issuer    = "https://login.microsoftonline.com/$tenantId/v2.0"
            subject   = $searchPrincipalId
            audiences = @('api://AzureADTokenExchange')
        }
        $tempFiles += $ficFile
        Invoke-Az 'Creazione federated credential' @('ad', 'app', 'federated-credential', 'create', '--id', $appId, '--parameters', "@$ficFile") | Out-Null
    }

    $connection = "SharePointOnlineEndpoint=$($siteUrl.TrimEnd('/'));ApplicationId=$appId;FederatedCredentialApplicationId=$searchClientId;TenantId=$tenantId"
    Save-EnvSetting 'SHAREPOINT_INDEXED_CONNECTION_STRING' $connection
    Write-Host "App di ingestion SharePoint pronta: '$displayName' (nessun segreto)." -ForegroundColor Green
}
finally {
    $tempFiles | ForEach-Object { Remove-Item $_ -ErrorAction SilentlyContinue }
}
