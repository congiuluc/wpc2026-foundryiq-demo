#!/usr/bin/env pwsh
# azd preprovision hook (also run by scripts/deploy_noazd.ps1): prepares the environment before the Bicep deployment.
#  - verifies the Azure CLI login (needed by the hooks for SQL, Storage and AI Search)
#  - sets the identity of the user running the demo (Foundry/Search/Storage roles + SQL Entra admin)
#  - generates the MCP server API key if missing
#  - checks the model quota (warning only)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot 'env_settings.ps1')

Write-Host '== preprovision: preparazione ambiente ==' -ForegroundColor Cyan

if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
    throw 'Azure CLI (az) non trovata. Installala da https://aka.ms/azcli: serve agli hook per SQL, Storage e AI Search.'
}

$subscriptionId = $env:AZURE_SUBSCRIPTION_ID
$tenantId = $env:AZURE_TENANT_ID
$account = az account show -o json 2>$null | ConvertFrom-Json
if (-not $account) {
    throw "Azure CLI non autenticata. Esegui: az login$(if ($tenantId) { " --tenant $tenantId" })"
}
if ($tenantId -and $account.tenantId -ne $tenantId) {
    throw "Azure CLI connessa al tenant $($account.tenantId), l'ambiente usa $tenantId. Esegui: az login --tenant $tenantId"
}
if ($subscriptionId -and $account.id -ne $subscriptionId) {
    Write-Host "Allineo la sottoscrizione di Azure CLI a $subscriptionId"
    az account set --subscription $subscriptionId | Out-Null
}

$user = az ad signed-in-user show --query '{id:id, upn:userPrincipalName}' -o json 2>$null | ConvertFrom-Json
if ($user) {
    if ((Get-EnvSetting 'AZURE_PRINCIPAL_ID') -ne $user.id) { Save-EnvSetting AZURE_PRINCIPAL_ID $user.id }
    if (-not (Get-EnvSetting 'AZURE_PRINCIPAL_TYPE')) { Save-EnvSetting AZURE_PRINCIPAL_TYPE 'User' }
    if (-not (Get-EnvSetting 'AZURE_PRINCIPAL_NAME')) { Save-EnvSetting AZURE_PRINCIPAL_NAME $user.upn }
    Write-Host "Utente demo: $($user.upn) ($($user.id))"
}
elseif (-not (Get-EnvSetting 'AZURE_PRINCIPAL_NAME')) {
    # Service principal login (e.g. CI): use the client id as principal and display name as SQL admin.
    $sp = az ad sp show --id $account.user.name --query '{id:id, name:displayName}' -o json | ConvertFrom-Json
    Save-EnvSetting AZURE_PRINCIPAL_ID $sp.id
    Save-EnvSetting AZURE_PRINCIPAL_TYPE 'ServicePrincipal'
    Save-EnvSetting AZURE_PRINCIPAL_NAME $sp.name
    Write-Host "Service principal: $($sp.name) ($($sp.id))"
}

$sqlPrivate = (Get-EnvSetting 'SQL_PRIVATE_ENDPOINT') -ne 'false'
if ($sqlPrivate -and (Get-EnvSetting 'APP_SERVICE_SKU') -eq 'F1') {
    throw "APP_SERVICE_SKU=F1 non supporta l'integrazione VNet richiesta dal private endpoint SQL. Usa B1 (azd env set APP_SERVICE_SKU B1) oppure azd env set SQL_PRIVATE_ENDPOINT false."
}

if (-not (Get-EnvSetting 'MCP_API_KEY')) {
    $bytes = [byte[]]::new(32)
    [System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    Save-EnvSetting MCP_API_KEY ([Convert]::ToHexString($bytes).ToLowerInvariant())
    Write-Host 'Generata MCP_API_KEY (salvata nell''ambiente azd o in .deploy/<env>.local.env)'
}

$location = if ($env:AZURE_LOCATION) { $env:AZURE_LOCATION } else { Get-EnvSetting 'AZURE_LOCATION' }
if (-not $location) {
    # azd needs AZURE_LOCATION for the subscription-scope deployment; same default as main.parameters.json.
    $location = 'swedencentral'
    Save-EnvSetting AZURE_LOCATION $location
    Write-Host "AZURE_LOCATION non impostata: uso $location"
}
if ($location) {
    $required = @(
        @{ Name = 'OpenAI.DataZoneStandard.gpt-5-mini'; Need = [int]($(Get-EnvSetting 'CHAT_CAPACITY') -replace '^$', '100') },
        @{ Name = 'OpenAI.Standard.text-embedding-3-large'; Need = [int]($(Get-EnvSetting 'EMBEDDING_CAPACITY') -replace '^$', '10') },
        @{ Name = 'OpenAI.GlobalStandard.gpt-5.2'; Need = [int]($(Get-EnvSetting 'GPT52_CAPACITY') -replace '^$', '100') }
    )
    try {
        $usages = az cognitiveservices usage list --location $location -o json 2>$null | ConvertFrom-Json
        foreach ($r in $required) {
            $u = $usages | Where-Object { $_.name.value -eq $r.Name } | Select-Object -First 1
            if (-not $u) { Write-Warning "Quota $($r.Name) non trovata in $location."; continue }
            $free = [int]($u.limit - $u.currentValue)
            if ($free -lt $r.Need) {
                Write-Warning "Quota $($r.Name) in ${location}: disponibili $free K TPM, richiesti $($r.Need). Riduci la capacita' (azd env set ...) o cambia regione."
            }
            else { Write-Host "Quota $($r.Name): ok ($free K TPM disponibili)" }
        }
    }
    catch { Write-Warning "Controllo quota non riuscito: $($_.Exception.Message)" }
}

Write-Host 'preprovision completato.' -ForegroundColor Green
