#!/usr/bin/env pwsh
# Prepares the Foundry IQ document-level ACL demo:
#  1. creates (if missing) two Entra security groups: "WPC FoundryIQ ACL - Tecnici" and "WPC FoundryIQ ACL - Direzione"
#     (override with FOUNDRYIQ_ACL_GROUP_TECNICI_ID / FOUNDRYIQ_ACL_GROUP_DIREZIONE_ID to reuse existing groups)
#  2. makes the signed-in user owner of both groups and member of Tecnici only
#  3. uploads data/acl to the ADLS Gen2 container and applies POSIX ACLs per folder (scripts/setup_acl_docs.py)
#  4. saves the group IDs in the azd env (read by setup_foundry_iq.ps1 and acl-demo.ipynb)
# Called by the azd postprovision hook; can be run manually. Needs permission to create groups in Entra ID
# (default for users, unless restricted by the tenant).
[CmdletBinding()]
param(
    [string]$TecniciGroupName = 'WPC FoundryIQ ACL - Tecnici',
    [string]$DirezioneGroupName = 'WPC FoundryIQ ACL - Direzione'
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$root = Split-Path -Parent $PSScriptRoot

. (Join-Path $PSScriptRoot 'env_settings.ps1')

function Get-OptionalSetting([string]$Name) { return Get-EnvSetting $Name }

function Get-Setting([string]$Name) {
    $value = Get-OptionalSetting $Name
    if (-not $value) { throw "Valore $Name mancante: esegui prima 'azd provision'." }
    return $value
}

function Invoke-Az([string]$What, [string[]]$Arguments) {
    $out = az @Arguments --only-show-errors 2>&1
    if ($LASTEXITCODE -ne 0) { throw "${What} fallito: $out" }
    return ($out | Out-String).Trim()
}

function Resolve-Group([string]$SettingName, [string]$DisplayName) {
    $id = Get-OptionalSetting $SettingName
    if ($id) {
        Invoke-Az "Lettura gruppo $id" @('ad', 'group', 'show', '--group', $id, '--query', 'id', '-o', 'tsv') | Out-Null
        return $id
    }
    $id = Invoke-Az "Ricerca gruppo '$DisplayName'" @('ad', 'group', 'list', '--filter', "displayName eq '$DisplayName'", '--query', '[0].id', '-o', 'tsv')
    if (-not $id) {
        $nickname = ($DisplayName -replace '[^A-Za-z0-9]', '').ToLowerInvariant()
        $id = Invoke-Az "Creazione gruppo '$DisplayName'" @('ad', 'group', 'create', '--display-name', $DisplayName, '--mail-nickname', $nickname,
            '--description', 'Gruppo della demo ACL Foundry IQ (wpc-foundryiq-demo).', '--query', 'id', '-o', 'tsv')
        Write-Host "  creato gruppo '$DisplayName' ($id)"
    }
    else { Write-Host "  gruppo esistente '$DisplayName' ($id)" }
    return $id
}

function Test-Member([string]$GroupId, [string]$MemberId) {
    return (Invoke-Az 'Verifica appartenenza' @('ad', 'group', 'member', 'check', '--group', $GroupId, '--member-id', $MemberId, '--query', 'value', '-o', 'tsv')) -eq 'true'
}

Write-Host 'Demo ACL Foundry IQ: gruppi Entra' -ForegroundColor Cyan
$tecniciId = Resolve-Group 'FOUNDRYIQ_ACL_GROUP_TECNICI_ID' $TecniciGroupName
$direzioneId = Resolve-Group 'FOUNDRYIQ_ACL_GROUP_DIREZIONE_ID' $DirezioneGroupName

$userId = (az ad signed-in-user show --query id -o tsv 2>$null | Out-String).Trim()
if ($LASTEXITCODE -eq 0 -and $userId) {
    foreach ($groupId in $tecniciId, $direzioneId) {
        # Owner lets the demo user change membership from the notebook without directory admin roles.
        $owners = Invoke-Az 'Lettura owner' @('ad', 'group', 'owner', 'list', '--group', $groupId, '--query', '[].id', '-o', 'tsv')
        if (($owners -split "`r?`n") -notcontains $userId) {
            Invoke-Az 'Aggiunta owner' @('ad', 'group', 'owner', 'add', '--group', $groupId, '--owner-object-id', $userId) | Out-Null
        }
    }
    if (-not (Test-Member $tecniciId $userId)) {
        Invoke-Az 'Aggiunta membro Tecnici' @('ad', 'group', 'member', 'add', '--group', $tecniciId, '--member-id', $userId) | Out-Null
    }
    Write-Host '  utente corrente: owner dei due gruppi, membro di Tecnici'
}
else {
    Write-Warning "Identita corrente non utente (service principal?): aggiungi manualmente gli utenti di test ai gruppi."
}

Write-Host 'Demo ACL Foundry IQ: documenti e ACL su ADLS Gen2' -ForegroundColor Cyan
$venvPython = if ($IsWindows) { Join-Path $root '.venv\Scripts\python.exe' } else { Join-Path $root '.venv/bin/python' }
if (-not (Test-Path $venvPython)) { $venvPython = 'python' }
$aclArgs = @((Join-Path $PSScriptRoot 'setup_acl_docs.py'),
    '--dfs-endpoint', (Get-Setting 'FOUNDRYIQ_ACL_DFS_ENDPOINT'),
    '--container', (Get-Setting 'FOUNDRYIQ_ACL_CONTAINER'),
    '--tecnici-group-id', $tecniciId,
    '--direzione-group-id', $direzioneId)
# Storage Blob Data Owner was just assigned by azd provision and can take a few minutes to propagate.
for ($i = 1; ; $i++) {
    & $venvPython @aclArgs
    if ($LASTEXITCODE -eq 0) { break }
    if ($i -ge 8) { throw "setup_acl_docs.py fallito (exit code $LASTEXITCODE)" }
    Write-Warning "Upload ACL: tentativo $i/8 fallito, riprovo tra 20 s (propagazione RBAC)..."
    Start-Sleep -Seconds 20
}

foreach ($pair in @(@('FOUNDRYIQ_ACL_GROUP_TECNICI_ID', $tecniciId), @('FOUNDRYIQ_ACL_GROUP_DIREZIONE_ID', $direzioneId))) {
    Save-EnvSetting $pair[0] $pair[1]
}
Write-Host 'Demo ACL pronta: la knowledge source viene creata da setup_foundry_iq.ps1 (postdeploy).' -ForegroundColor Green
