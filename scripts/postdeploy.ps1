#!/usr/bin/env pwsh
# azd postdeploy hook: checks the MCP server deployed to App Service and configures Foundry IQ.
#  1. waits for /healthz (first start of App Service + Oryx build, SQL resume)
#  2. smoke test of the MCP endpoint (tools/list with x-api-key)
#  3. creates/updates the knowledge sources, the knowledge base and the project connections (scripts/setup_foundry_iq.ps1)
#  4. creates/updates the Foundry prompt agents declared in agents/*.yaml (scripts/setup_foundry_agents.py)
# Set SKIP_FOUNDRY_IQ_SETUP=true to skip step 3 and SKIP_FOUNDRY_AGENTS_SETUP=true to skip step 4.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot 'env_settings.ps1')

if (-not $env:MCP_HEALTH_URL -or -not $env:MCP_SERVER_URL -or -not $env:MCP_API_KEY) {
    # Run by hand outside azd hooks: load azd env values or Bicep outputs + local state.
    & (Join-Path $PSScriptRoot 'load_env.ps1') -Quiet
}
$healthUrl = $env:MCP_HEALTH_URL
$mcpUrl = $env:MCP_SERVER_URL
if (-not $healthUrl -or -not $mcpUrl) { throw 'MCP_HEALTH_URL / MCP_SERVER_URL mancanti: la fase provision non è completa.' }
$failed = [Collections.Generic.List[string]]::new()

Write-Host '== postdeploy 1/4: health check MCP server ==' -ForegroundColor Cyan
$health = $null
for ($i = 1; $i -le 30 -and -not $health; $i++) {
    try { $health = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 30 }
    catch {
        Write-Host "  in attesa dell'avvio ($i/30)..."
        Start-Sleep -Seconds 10
    }
}
if (-not $health) { throw "Il server MCP non risponde su $healthUrl. Controlla i log: az webapp log tail -g $env:AZURE_RESOURCE_GROUP -n $env:MCP_APP_NAME" }
$inv = $health.inventory
Write-Host "  stato: $($health.status), sorgente dati: $($inv.source), ricambi: $($inv.parts)"
if ($inv.source -ne 'sql') {
    Write-Warning "Il server non legge da Azure SQL (sorgente '$($inv.source)'$(if ($inv.PSObject.Properties['error']) { ": $($inv.error)" })). Usa il catalogo JSON incluso come fallback."
}

Write-Host '== postdeploy 2/4: smoke test MCP (tools/list) ==' -ForegroundColor Cyan
$headers = @{ 'x-api-key' = $env:MCP_API_KEY; Accept = 'application/json, text/event-stream' }
$request = @{ jsonrpc = '2.0'; id = 1; method = 'tools/list'; params = @{} } | ConvertTo-Json -Compress
$response = Invoke-RestMethod -Method Post -Uri $mcpUrl -Headers $headers -ContentType 'application/json' -Body $request -TimeoutSec 60
$tools = @($response.result.tools | ForEach-Object name)
if ($tools.Count -eq 0) { throw 'tools/list non ha restituito tool.' }
Write-Host "  tool disponibili: $($tools -join ', ')"

Write-Host '== postdeploy 3/4: knowledge source e knowledge base Foundry IQ ==' -ForegroundColor Cyan
$foundryIqOk = $true
if ($env:SKIP_FOUNDRY_IQ_SETUP -eq 'true') {
    Write-Host '  saltato (SKIP_FOUNDRY_IQ_SETUP=true).'
}
else {
    try { & (Join-Path $PSScriptRoot 'setup_foundry_iq.ps1') }
    catch {
        $foundryIqOk = $false
        Write-Warning "Configurazione Foundry IQ non riuscita: $($_.Exception.Message)"
        $failed.Add('Foundry IQ (knowledge source, KB, connessioni) -> ./scripts/setup_foundry_iq.ps1')
    }
}

Write-Host '== postdeploy 4/4: agenti Foundry (agents/*.yaml) ==' -ForegroundColor Cyan
if ($env:SKIP_FOUNDRY_AGENTS_SETUP -eq 'true') {
    Write-Host '  saltato (SKIP_FOUNDRY_AGENTS_SETUP=true).'
}
elseif (-not $foundryIqOk) {
    # The agents reference the KB endpoint and the connections created in step 3: do not deploy them half configured.
    Write-Warning 'Saltato: gli agenti usano la knowledge base e le connessioni del passo 3, non riuscito.'
    $failed.Add('agenti Foundry (dopo il passo 3) -> .venv/Scripts/python ./scripts/setup_foundry_agents.py')
}
else {
    $root = Split-Path $PSScriptRoot -Parent
    $venvPython = if ($IsWindows) { Join-Path $root '.venv\Scripts\python.exe' } else { Join-Path $root '.venv/bin/python' }
    if (-not (Test-Path $venvPython)) { $venvPython = 'python' }
    & $venvPython (Join-Path $PSScriptRoot 'setup_foundry_agents.py')
    if ($LASTEXITCODE -ne 0) {
        $failed.Add("agenti Foundry (exit code $LASTEXITCODE) -> .venv/Scripts/python ./scripts/setup_foundry_agents.py")
    }
}

if ($failed.Count -gt 0) {
    # The web apps are already deployed and running: only the Foundry configuration must be resumed.
    Write-Host ''
    Write-Warning "postdeploy: $($failed.Count) passi non riusciti (le Web App sono già pubblicate e funzionanti):"
    $failed | ForEach-Object { Write-Warning "  - $_" }
    throw "Configurazione Foundry incompleta. Dopo aver corretto la causa riprendi con '$(Get-ResumeCommand postdeploy)' (non serve ripetere provision o deploy). Per saltare: SKIP_FOUNDRY_IQ_SETUP=true / SKIP_FOUNDRY_AGENTS_SETUP=true."
}
Write-Host ''
Write-Host "MCP server: $mcpUrl" -ForegroundColor Green
Write-Host 'Chiave API: azd env get-value MCP_API_KEY'
