<#
.SYNOPSIS
    Genera notebooks\demo\.env per i notebook demo (CONFIG_SOURCE = "env").
.DESCRIPTION
    Legge l'ambiente azd selezionato (o -EnvironmentName); senza azd usa .deploy/<ambiente>.local.env.
    Scrive solo impostazioni non segrete (le stesse di AZD_KEYS in scripts/notebook_env.py):
    identità dell'ambiente, endpoint, nomi KB, ID gruppi ACL e URL delle policy. Niente token o chiavi.
.EXAMPLE
    ./notebooks/demo/New-NotebookEnv.ps1
    ./notebooks/demo/New-NotebookEnv.ps1 -EnvironmentName wpc-demo
#>
[CmdletBinding()]
param(
    [string]$EnvironmentName = '',
    [string]$OutFile = (Join-Path $PSScriptRoot '.env')
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '..\..\scripts\env_settings.ps1')

# Allineato ad AZD_KEYS in scripts/notebook_env.py.
$keys = @(
    'AZURE_ENV_NAME', 'AZURE_SUBSCRIPTION_ID', 'AZURE_TENANT_ID',
    'AZURE_RESOURCE_GROUP', 'AZURE_LOCATION', 'AZURE_DEPLOYMENT_NAME',
    'FOUNDRYIQ_DEMO_SEARCH_ENDPOINT', 'FOUNDRYIQ_DEMO_OPENAI_ENDPOINT',
    'FOUNDRYIQ_DEMO_CHAT_DEPLOYMENT', 'FOUNDRYIQ_DEMO_GPT52_DEPLOYMENT',
    'FOUNDRYIQ_DEMO_PROJECT_ENDPOINT', 'FOUNDRYIQ_DEMO_KB_NAME',
    'FOUNDRYIQ_ACL_KB_NAME', 'FOUNDRYIQ_ACL_DFS_ENDPOINT',
    'FOUNDRYIQ_ACL_CONTAINER', 'FOUNDRYIQ_ACL_GROUP_TECNICI_ID',
    'FOUNDRYIQ_ACL_GROUP_DIREZIONE_ID', 'SHAREPOINT_POLICIES_URL'
)
$values = Get-AllEnvSettings $EnvironmentName

$missing = @('AZURE_SUBSCRIPTION_ID', 'AZURE_TENANT_ID', 'AZURE_RESOURCE_GROUP', 'FOUNDRYIQ_DEMO_SEARCH_ENDPOINT') |
    Where-Object { -not $values[$_] }
if ($missing) { throw "Variabili mancanti nell'ambiente: $($missing -join ', ')" }

$lines = @('# Generato da notebooks/demo/New-NotebookEnv.ps1: solo impostazioni non segrete.')
foreach ($key in $keys) {
    if ($values[$key]) { $lines += "$key=$($values[$key])" }
}
$lines | Set-Content -LiteralPath $OutFile -Encoding utf8NoBOM

Write-Host "Scritto $OutFile ($($lines.Count - 1) impostazioni). Nei notebook usa CONFIG_SOURCE = `"env`"." -ForegroundColor Green
