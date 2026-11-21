#!/usr/bin/env pwsh
# Loads the deployment values into the current PowerShell session, so hooks and setup_* scripts can be
# (re)run by hand, also in a new session or after a failed step.
#  - with azd (default when installed and an environment exists): azd env get-values
#  - without azd (-NoAzd, or azd missing): local state file .deploy/<env>.local.env + outputs of the
#    Bicep deployment (az deployment sub show), which always win because they reflect Azure.
# Usage: ./scripts/load_env.ps1 [-EnvironmentName <env>] [-NoAzd] [-DeploymentName <name>]
[CmdletBinding()]
param(
    [string]$EnvironmentName = $env:AZURE_ENV_NAME,
    [switch]$NoAzd,
    [string]$DeploymentName,
    [switch]$Quiet
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if ($NoAzd) { $env:WPC_NO_AZD = 'true' }
. (Join-Path $PSScriptRoot 'env_settings.ps1')

function Set-SessionValue([string]$Name, $Value) {
    if ($Value -is [bool]) { $Value = "$Value".ToLowerInvariant() }
    [Environment]::SetEnvironmentVariable($Name, [string]$Value)
}

if (Test-AzdAvailable) {
    $azdArgs = @('env', 'get-values', '--output', 'json')
    if ($EnvironmentName) { $azdArgs += @('--environment', $EnvironmentName) }
    $json = (azd @azdArgs 2>$null | Out-String)
    if ($LASTEXITCODE -eq 0 -and $json.Trim()) {
        $values = $json | ConvertFrom-Json
        foreach ($p in $values.PSObject.Properties) { Set-SessionValue $p.Name $p.Value }
        if (-not $Quiet) { Write-Host "Caricati $(@($values.PSObject.Properties).Count) valori dall'ambiente azd '$env:AZURE_ENV_NAME'." -ForegroundColor Green }
        return
    }
    if (-not $EnvironmentName) {
        throw "Nessun ambiente azd selezionato: usa 'azd env select <env>' oppure -EnvironmentName <env> (-NoAzd per l'opzione senza azd)."
    }
    Write-Warning "Ambiente azd '$EnvironmentName' non trovato: uso il file di stato locale e gli output Bicep."
    $env:WPC_NO_AZD = 'true'
}

if (-not $EnvironmentName) { throw 'Specifica -EnvironmentName <env> (lo stesso usato per il deployment).' }
$env:AZURE_ENV_NAME = $EnvironmentName

$state = Read-StateFile $EnvironmentName
foreach ($name in $state.Keys) { Set-SessionValue $name $state[$name] }

if (-not $DeploymentName) { $DeploymentName = if ($state.Contains('AZURE_DEPLOYMENT_NAME')) { $state['AZURE_DEPLOYMENT_NAME'] } else { "wpc-foundryiq-$EnvironmentName" } }
$outputs = az deployment sub show -n $DeploymentName --query properties.outputs -o json 2>$null | Out-String
$outputCount = 0
if ($LASTEXITCODE -eq 0 -and $outputs.Trim() -and $outputs.Trim() -ne 'null') {
    foreach ($o in ($outputs | ConvertFrom-Json).PSObject.Properties) {
        Set-SessionValue $o.Name $o.Value.value
        $outputCount++
    }
}
elseif (-not $Quiet) {
    Write-Warning "Deployment Bicep '$DeploymentName' non trovato o senza output: esegui (o riprendi) la fase provision."
}
if (-not $env:AZURE_SUBSCRIPTION_ID) { $env:AZURE_SUBSCRIPTION_ID = (az account show --query id -o tsv 2>$null | Out-String).Trim() }

if (-not $Quiet) {
    Write-Host "Ambiente '$EnvironmentName' senza azd: $($state.Count) valori da $(Get-StateFilePath $EnvironmentName), $outputCount output dal deployment '$DeploymentName'." -ForegroundColor Green
}
