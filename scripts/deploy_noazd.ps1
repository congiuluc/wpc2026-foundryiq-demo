#!/usr/bin/env pwsh
# Option B1 (without azd): same Bicep modules and same hook scripts as `azd up`, driven by Azure CLI.
# Every phase is idempotent and the values are kept in .deploy/<env>.local.env (git-ignored), so after
# an error you can resume from the failed phase, also in a new session:
#   ./scripts/deploy_noazd.ps1 -EnvironmentName wpc-foundryiq -Location swedencentral   # all phases
#   ./scripts/deploy_noazd.ps1 -EnvironmentName wpc-foundryiq -From deploy               # resume
#   ./scripts/deploy_noazd.ps1 -EnvironmentName wpc-foundryiq -Only postdeploy           # one phase
# Bicep parameters come from the same variables as infra/main.parameters.json (e.g. $env:APP_SERVICE_SKU,
# $env:SQL_PRIVATE_ENDPOINT, $env:CHAT_CAPACITY) and are saved in the state file at the first run.
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$EnvironmentName,
    [string]$Location,
    [string]$SubscriptionId,
    [ValidateSet('preprovision', 'provision', 'postprovision', 'deploy', 'postdeploy')][string]$From = 'preprovision',
    [ValidateSet('preprovision', 'provision', 'postprovision', 'deploy', 'postdeploy')][string[]]$Only
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$root = Split-Path $PSScriptRoot -Parent
$env:WPC_NO_AZD = 'true'
$env:AZURE_ENV_NAME = $EnvironmentName
. (Join-Path $PSScriptRoot 'env_settings.ps1')

if (-not (Get-Command az -ErrorAction SilentlyContinue)) { throw 'Azure CLI (az) non trovata: https://aka.ms/azcli' }
& (Join-Path $PSScriptRoot 'load_env.ps1') -EnvironmentName $EnvironmentName -NoAzd -Quiet

Save-EnvSetting 'AZURE_ENV_NAME' $EnvironmentName
if ($Location) { Save-EnvSetting 'AZURE_LOCATION' $Location }
if ($SubscriptionId) { Save-EnvSetting 'AZURE_SUBSCRIPTION_ID' $SubscriptionId }
$deploymentName = Get-EnvSetting 'AZURE_DEPLOYMENT_NAME'
if (-not $deploymentName) { $deploymentName = "wpc-foundryiq-$EnvironmentName"; Save-EnvSetting 'AZURE_DEPLOYMENT_NAME' $deploymentName }

function Get-Param([string]$Name, [string]$Default = '') {
    $value = Get-EnvSetting $Name
    if (-not $value) { $value = $Default }
    return $value
}

function Invoke-Provision {
    # Persist the infrastructure choices, so a resumed provision redeploys the same configuration.
    foreach ($name in 'APP_SERVICE_SKU', 'SQL_PRIVATE_ENDPOINT', 'SQL_USE_FREE_LIMIT', 'SQL_LOCATION', 'CHAT_CAPACITY', 'EMBEDDING_CAPACITY', 'GPT52_CAPACITY') {
        $value = [Environment]::GetEnvironmentVariable($name)
        if ($value) { Save-EnvSetting $name $value }
    }
    $parameters = [ordered]@{
        environmentName       = $EnvironmentName
        location              = Get-Param 'AZURE_LOCATION' 'swedencentral'
        demoUserObjectId      = Get-Param 'AZURE_PRINCIPAL_ID'
        demoUserPrincipalType = Get-Param 'AZURE_PRINCIPAL_TYPE' 'User'
        sqlAdminLogin         = Get-Param 'AZURE_PRINCIPAL_NAME'
        chatCapacity          = [int](Get-Param 'CHAT_CAPACITY' '100')
        embeddingCapacity     = [int](Get-Param 'EMBEDDING_CAPACITY' '10')
        gpt52Capacity         = [int](Get-Param 'GPT52_CAPACITY' '100')
        appServiceSkuName     = Get-Param 'APP_SERVICE_SKU' 'B1'
        sqlLocation           = Get-Param 'SQL_LOCATION'
        sqlUseFreeLimit       = (Get-Param 'SQL_USE_FREE_LIMIT' 'true') -eq 'true'
        sqlPrivateEndpoint    = (Get-Param 'SQL_PRIVATE_ENDPOINT' 'true') -eq 'true'
        mcpApiKey             = Get-Param 'MCP_API_KEY'
        chatAuthClientId      = Get-Param 'CHAT_APP_CLIENT_ID'
    }
    foreach ($required in 'demoUserObjectId', 'sqlAdminLogin', 'mcpApiKey') {
        if (-not $parameters[$required]) { throw "Parametro $required mancante: esegui prima la fase preprovision." }
    }
    $body = @{
        '$schema'      = 'https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#'
        contentVersion = '1.0.0.0'
        parameters     = [ordered]@{}
    }
    foreach ($key in $parameters.Keys) { $body.parameters[$key] = @{ value = $parameters[$key] } }
    # The parameters file contains the MCP API key: keep it outside the repo and delete it afterwards.
    $file = Join-Path ([IO.Path]::GetTempPath()) "wpc-foundryiq-$([guid]::NewGuid().ToString('N')).parameters.json"
    try {
        $body | ConvertTo-Json -Depth 10 | Set-Content -Path $file -Encoding utf8NoBOM
        az deployment sub create -n $deploymentName -l $parameters.location -f (Join-Path $root 'infra/main.bicep') `
            -p "@$file" --only-show-errors -o none
        if ($LASTEXITCODE -ne 0) { throw "az deployment sub create fallito (exit code $LASTEXITCODE). Dettagli: az deployment operation sub list -n $deploymentName --query `"[?properties.provisioningState=='Failed']`"" }
    }
    finally { Remove-Item $file -Force -ErrorAction SilentlyContinue }
    & (Join-Path $PSScriptRoot 'load_env.ps1') -EnvironmentName $EnvironmentName -NoAzd
}

function Invoke-Deploy {
    foreach ($name in 'AZURE_RESOURCE_GROUP', 'MCP_APP_NAME', 'CHAT_APP_NAME') {
        if (-not [Environment]::GetEnvironmentVariable($name)) { throw "Variabile $name mancante: esegui prima la fase provision." }
    }
    foreach ($svc in @(@{ Dir = 'mcp-ricambi'; App = $env:MCP_APP_NAME }, @{ Dir = 'webapp-chat'; App = $env:CHAT_APP_NAME })) {
        Write-Host "  pubblicazione $($svc.Dir) su $($svc.App) (build Oryx lato App Service)..."
        $zip = Join-Path ([IO.Path]::GetTempPath()) "$($svc.Dir)-$([guid]::NewGuid().ToString('N')).zip"
        try {
            Get-ChildItem (Join-Path $root $svc.Dir) -Force -Exclude '.venv', '__pycache__', '.pytest_cache', 'tests', '.env' |
                Compress-Archive -DestinationPath $zip -Force
            az webapp deploy -g $env:AZURE_RESOURCE_GROUP -n $svc.App --src-path $zip --type zip --only-show-errors -o none
            if ($LASTEXITCODE -ne 0) { throw "az webapp deploy di $($svc.Dir) fallito (exit code $LASTEXITCODE)" }
        }
        finally { Remove-Item $zip -Force -ErrorAction SilentlyContinue }
    }
}

$phases = [ordered]@{
    preprovision  = { & (Join-Path $PSScriptRoot 'preprovision.ps1') }
    provision     = { Invoke-Provision }
    postprovision = { & (Join-Path $PSScriptRoot 'postprovision.ps1') }
    deploy        = { Invoke-Deploy }
    postdeploy    = { & (Join-Path $PSScriptRoot 'postdeploy.ps1') }
}
$names = @($phases.Keys)
$selected = if ($Only) { $names | Where-Object { $_ -in $Only } } else { $names[$names.IndexOf($From)..($names.Count - 1)] }

foreach ($phase in $selected) {
    Write-Host "`n#### fase $phase (ambiente $EnvironmentName, senza azd) ####" -ForegroundColor Magenta
    try { & $phases[$phase] }
    catch {
        Write-Host "`nFase '$phase' non riuscita: $($_.Exception.Message)" -ForegroundColor Red
        Write-Host "Correggi la causa e riprendi da qui: ./scripts/deploy_noazd.ps1 -EnvironmentName $EnvironmentName -From $phase" -ForegroundColor Yellow
        throw
    }
}
Write-Host "`nDeployment completato ($($selected -join ', ')). Valori salvati in $(Get-StateFilePath)." -ForegroundColor Green
if ($env:CHAT_APP_URL) { Write-Host "Chat web app: $env:CHAT_APP_URL" }
