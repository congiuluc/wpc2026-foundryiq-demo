#!/usr/bin/env pwsh
# azd postprovision hook: post-provisioning steps that used to be manual in the README.
#  1. creates the Python .venv in the repo root (shared by the notebook and the hooks)
#  2. opens the SQL firewall to the current client IP and loads the spare parts catalog into Azure SQL
#  3. grants the MCP App Service managed identity read access on the database
#  4. uploads data/machinespecs to the Blob container for the Foundry IQ knowledge source
#  5. creates the SharePoint site if needed and uploads data/sharepoint/policies (scripts/setup_sharepoint.ps1)
#  6. ACL demo: Entra groups + documents with POSIX ACLs on ADLS Gen2 (scripts/setup_acl_demo.ps1)
#  7. chat web app: Entra sign-in with App Service authentication (scripts/setup_chat_auth.ps1)
#  8. prints the values for the notebook
# Set SKIP_SHAREPOINT_SETUP=true to skip step 5, SKIP_ACL_DEMO_SETUP=true to skip step 6, SKIP_CHAT_AUTH_SETUP=true to skip step 7.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot 'env_settings.ps1')

$root = Split-Path -Parent $PSScriptRoot
$requiredVars = 'AZURE_RESOURCE_GROUP', 'SQL_SERVER_NAME', 'SQL_SERVER_FQDN', 'SQL_DATABASE_NAME', 'STORAGE_ACCOUNT_NAME', 'MCP_APP_NAME', 'MCP_APP_PRINCIPAL_ID'
if ($requiredVars | Where-Object { -not [Environment]::GetEnvironmentVariable($_) }) {
    # Run by hand outside azd hooks: load azd env values or Bicep outputs + local state.
    & (Join-Path $PSScriptRoot 'load_env.ps1') -Quiet
}
foreach ($name in $requiredVars) {
    if (-not [Environment]::GetEnvironmentVariable($name)) { throw "Variabile $name mancante: la fase provision non è completa (azd provision, oppure deploy_noazd.ps1 -From provision)." }
}
$rg = $env:AZURE_RESOURCE_GROUP
$failed = [Collections.Generic.List[string]]::new()

function Invoke-WithRetry([scriptblock]$Action, [string]$What, [int]$Attempts = 6, [int]$DelaySeconds = 20) {
    for ($i = 1; $i -le $Attempts; $i++) {
        try { & $Action; return }
        catch {
            if ($i -eq $Attempts) { throw }
            Write-Warning "${What}: tentativo $i/$Attempts fallito ($($_.Exception.Message)). Riprovo tra $DelaySeconds s..."
            Start-Sleep -Seconds $DelaySeconds
        }
    }
}

function Invoke-Native([string]$What, [scriptblock]$Command) {
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "$What fallito (exit code $LASTEXITCODE)" }
}

Write-Host '== postprovision 1/7: ambiente Python ==' -ForegroundColor Cyan
$venvPython = if ($IsWindows) { Join-Path $root '.venv\Scripts\python.exe' } else { Join-Path $root '.venv/bin/python' }
if (-not (Test-Path $venvPython)) {
    $basePython = if (Get-Command py -ErrorAction SilentlyContinue) { @('py', '-3') } elseif (Get-Command python3 -ErrorAction SilentlyContinue) { @('python3') } else { @('python') }
    Invoke-Native 'Creazione .venv' { & $basePython[0] @($basePython | Select-Object -Skip 1) -m venv (Join-Path $root '.venv') }
}
Invoke-Native 'pip install' {
    & $venvPython -m pip install --quiet --disable-pip-version-check -r (Join-Path $root 'requirements.txt') -r (Join-Path $root 'scripts/requirements.txt')
}

Write-Host '== postprovision 2/7: Azure SQL (firewall + dati) ==' -ForegroundColor Cyan
if ($env:SQL_PRIVATE_ENDPOINT -eq 'true') {
    # Private endpoint only: this machine cannot reach SQL. The MCP server (SQL Entra admin through its
    # user-assigned identity) creates the schema and loads data/inventory.json on first access.
    Write-Host '  SQL raggiungibile solo via private endpoint: il server MCP carica i dati al primo avvio.'
}
else {
    try {
        $clientIp = (Invoke-RestMethod -Uri 'https://api.ipify.org' -TimeoutSec 15).Trim()
        Invoke-Native 'Regola firewall SQL' {
            az sql server firewall-rule create -g $rg -s $env:SQL_SERVER_NAME -n 'azd-client' `
                --start-ip-address $clientIp --end-ip-address $clientIp -o none
        }
        Write-Host "Firewall SQL aperto per l'IP client $clientIp"
    }
    catch { Write-Warning "Impossibile configurare il firewall per l'IP client: $($_.Exception.Message)" }

    $appClientId = (az ad sp show --id $env:MCP_APP_PRINCIPAL_ID --query appId -o tsv 2>$null | Out-String).Trim()
    $seedArgs = @((Join-Path $root 'scripts/seed_sql.py'), '--server', $env:SQL_SERVER_FQDN, '--database', $env:SQL_DATABASE_NAME)
    if ($appClientId) { $seedArgs += @('--app-name', $env:MCP_APP_NAME, '--app-client-id', $appClientId) }
    else { Write-Warning "appId dell'identita gestita non trovato: il server MCP usera il fallback JSON finche non concedi l'accesso." }
    # Firewall rules and a paused serverless database can take a little while to become reachable.
    Invoke-WithRetry -What 'Caricamento dati SQL' -Attempts 4 -DelaySeconds 30 -Action {
        Invoke-Native 'seed_sql.py' { & $venvPython @seedArgs }
    }
}

Write-Host '== postprovision 3/7: upload machinespecs su Blob Storage ==' -ForegroundColor Cyan
$container = if ($env:FOUNDRYIQ_DEMO_BLOB_CONTAINER) { $env:FOUNDRYIQ_DEMO_BLOB_CONTAINER } else { 'machinespecs' }
# Role assignments created a few seconds ago may still be propagating.
Invoke-WithRetry -What 'Upload Blob' -Attempts 8 -DelaySeconds 20 -Action {
    Invoke-Native 'az storage blob upload-batch' {
        az storage blob upload-batch --account-name $env:STORAGE_ACCOUNT_NAME --destination $container `
            --source (Join-Path $root 'data/machinespecs') --auth-mode login --overwrite --only-show-errors -o none
    }
}
Write-Host "File caricati nel container '$container'."

Write-Host '== postprovision 4/7: sito SharePoint e documenti policies ==' -ForegroundColor Cyan
if ($env:SKIP_SHAREPOINT_SETUP -eq 'true') {
    Write-Host '  saltato (SKIP_SHAREPOINT_SETUP=true).'
}
else {
    # Non-blocking: needs Microsoft 365 in the same tenant and Graph consent (Sites.ReadWrite.All, Sites.Create.All).
    try { & (Join-Path $PSScriptRoot 'setup_sharepoint.ps1') }
    catch {
        Write-Warning "Configurazione SharePoint non riuscita: $($_.Exception.Message)"
        $failed.Add('SharePoint -> ./scripts/setup_sharepoint.ps1 (per saltarlo: SKIP_SHAREPOINT_SETUP=true)')
    }
}

Write-Host '== postprovision 5/7: demo ACL (gruppi Entra + documenti ADLS Gen2) ==' -ForegroundColor Cyan
if ($env:SKIP_ACL_DEMO_SETUP -eq 'true') {
    Write-Host '  saltato (SKIP_ACL_DEMO_SETUP=true).'
}
else {
    # Non-blocking: needs permission to create Entra security groups.
    try { & (Join-Path $PSScriptRoot 'setup_acl_demo.ps1') }
    catch {
        Write-Warning "Configurazione demo ACL non riuscita: $($_.Exception.Message)"
        $failed.Add('demo ACL -> ./scripts/setup_acl_demo.ps1 (per saltarla: SKIP_ACL_DEMO_SETUP=true)')
    }
}

Write-Host '== postprovision 6/7: login Entra per la chat web app ==' -ForegroundColor Cyan
if ($env:SKIP_CHAT_AUTH_SETUP -eq 'true') {
    Write-Host '  saltato (SKIP_CHAT_AUTH_SETUP=true): la chat web app resta bloccata (HTTP 401) finche non si esegue scripts/setup_chat_auth.ps1.'
}
else {
    # Non-blocking but fail-closed: without this step Bicep keeps authentication required with no identity provider (HTTP 401).
    try { & (Join-Path $PSScriptRoot 'setup_chat_auth.ps1') }
    catch {
        Write-Warning "Configurazione login chat web app non riuscita: $($_.Exception.Message)"
        $failed.Add('login chat (HTTP 401 finché non riesce) -> ./scripts/setup_chat_auth.ps1')
    }
}

Write-Host '== postprovision 7/7: valori per il notebook ==' -ForegroundColor Cyan
Write-Host "CHAT_APP_URL          = `"$env:CHAT_APP_URL`""
Write-Host "SUBSCRIPTION_ID       = `"$env:AZURE_SUBSCRIPTION_ID`""
Write-Host "RESOURCE_GROUP_NAME   = `"$rg`""
Write-Host "BICEP_DEPLOYMENT_NAME = `"$(if ($env:DEMO_DEPLOYMENT_NAME) { $env:DEMO_DEPLOYMENT_NAME } else { 'wpc-foundryiq-demo' })`""
if ($failed.Count -gt 0) {
    # Optional steps: provisioning stays valid, deploy continues. Each script is idempotent and can be rerun alone.
    Write-Warning "postprovision completato con $($failed.Count) passi opzionali non riusciti:"
    $failed | ForEach-Object { Write-Warning "  - $_" }
    Write-Warning "Dopo aver corretto la causa: rilancia lo script indicato oppure tutto il passo con '$(Get-ResumeCommand postprovision)'."
}
else { if ($failed.Count -gt 0) {
    # Optional steps: provisioning stays valid, deploy continues. Each script is idempotent and can be rerun alone.
    Write-Warning "postprovision completato con $($failed.Count) passi opzionali non riusciti:"
    $failed | ForEach-Object { Write-Warning "  - $_" }
    Write-Warning "Dopo aver corretto la causa: rilancia lo script indicato oppure tutto il passo con '$(Get-ResumeCommand postprovision)'."
}
else { Write-Host 'postprovision completato.' -ForegroundColor Green } }
