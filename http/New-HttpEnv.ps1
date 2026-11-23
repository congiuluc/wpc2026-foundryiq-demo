<#
.SYNOPSIS
    Genera http\.env per le demo REST Client (endpoint, nomi KB e token Azure CLI).
.DESCRIPTION
    Legge l'ambiente azd selezionato (o -EnvironmentName); senza azd usa .deploy/<ambiente>.local.env.
    Ottiene i token con Azure CLI per l'utente connesso e li scrive IN CHIARO nel file (ignorato da Git).
    I token non vengono stampati. Rieseguire alla scadenza dei token o dopo un cambio di utente.
.EXAMPLE
    ./http/New-HttpEnv.ps1
    ./http/New-HttpEnv.ps1 -EnvironmentName wpc-demo -SkipFoundryToken
#>
[CmdletBinding()]
param(
    [string]$EnvironmentName = '',
    [string]$OutFile = (Join-Path $PSScriptRoot '.env'),
    # Solo demo Search (multi-fonte/ACL): non richiede endpoint e token Foundry.
    [switch]$SkipFoundryToken
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '..\scripts\env_settings.ps1')

if (-not (Get-Command az -ErrorAction SilentlyContinue)) { throw 'Azure CLI non trovata nel PATH.' }
$values = Get-AllEnvSettings $EnvironmentName

$required = @('AZURE_TENANT_ID', 'FOUNDRYIQ_DEMO_SEARCH_ENDPOINT')
if (-not $SkipFoundryToken) { $required += 'FOUNDRYIQ_DEMO_PROJECT_ENDPOINT' }
$missing = $required | Where-Object { -not $values[$_] }
if ($missing) { throw "Variabili mancanti nell'ambiente: $($missing -join ', ')" }

function Get-DemoToken([string]$Resource) {
    $token = az account get-access-token --tenant $values['AZURE_TENANT_ID'] `
        --resource $Resource --query accessToken -o tsv --only-show-errors
    if ($LASTEXITCODE -ne 0 -or -not $token) {
        throw "Token non disponibile per $Resource (esegui 'az login --tenant $($values['AZURE_TENANT_ID'])')."
    }
    return ([string]$token).Trim()
}

$kbName = if ($values['FOUNDRYIQ_DEMO_KB_NAME']) { $values['FOUNDRYIQ_DEMO_KB_NAME'] } else { 'wpc-foundryiq-kb' }
$aclKbName = if ($values['FOUNDRYIQ_ACL_KB_NAME']) { $values['FOUNDRYIQ_ACL_KB_NAME'] } else { 'wpc-acl-kb' }
$searchToken = Get-DemoToken 'https://search.azure.com'
$foundryToken = if ($SkipFoundryToken) { '' } else { Get-DemoToken 'https://ai.azure.com' }
$projectEndpoint = ([string]$values['FOUNDRYIQ_DEMO_PROJECT_ENDPOINT']).TrimEnd('/')

@(
    '# Generato da http/New-HttpEnv.ps1: contiene token in chiaro, non condividere. Eliminare dopo la demo.'
    "FOUNDRYIQ_DEMO_SEARCH_ENDPOINT=$($values['FOUNDRYIQ_DEMO_SEARCH_ENDPOINT'].TrimEnd('/'))"
    "FOUNDRYIQ_DEMO_KB_NAME=$kbName"
    "FOUNDRYIQ_ACL_KB_NAME=$aclKbName"
    "FOUNDRYIQ_DEMO_PROJECT_ENDPOINT=$projectEndpoint"
    "SEARCH_TOKEN=$searchToken"
    "USER_SEARCH_TOKEN=$searchToken"
    "FOUNDRY_TOKEN=$foundryToken"
) | Set-Content -LiteralPath $OutFile -Encoding utf8NoBOM
Remove-Variable searchToken, foundryToken

Write-Host "Scritto $OutFile (token validi circa 1 ora; il file contiene token in chiaro)." -ForegroundColor Green
