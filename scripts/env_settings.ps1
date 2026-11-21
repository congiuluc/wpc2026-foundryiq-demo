# Helper shared by the hooks and the setup_* scripts: read and save settings with or without azd.
# - Read: process env var, then the active azd environment (if azd is used), then the local state file.
# - Save: always in the current process; in the azd env when azd is used, otherwise in the local state file
#   .deploy/<AZURE_ENV_NAME>.local.env (git-ignored), so a new session can resume with scripts/load_env.ps1.
# WPC_NO_AZD=true (set by scripts/deploy_noazd.ps1) ignores azd even when it is installed.

function Test-AzdAvailable {
    if ($env:WPC_NO_AZD -eq 'true') { return $false }
    return [bool](Get-Command azd -ErrorAction SilentlyContinue)
}

function Get-StateFilePath([string]$EnvironmentName = $env:AZURE_ENV_NAME) {
    if (-not $EnvironmentName) { return $null }
    return Join-Path (Split-Path $PSScriptRoot -Parent) ".deploy/$EnvironmentName.local.env"
}

function Read-StateFile([string]$EnvironmentName = $env:AZURE_ENV_NAME) {
    $values = [ordered]@{}
    $path = Get-StateFilePath $EnvironmentName
    if ($path -and (Test-Path $path)) {
        foreach ($line in Get-Content $path) {
            if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$') { $values[$Matches[1]] = $Matches[2] }
        }
    }
    return $values
}

function Write-StateValue([string]$Name, [string]$Value) {
    $path = Get-StateFilePath
    if (-not $path) { return $false }
    $values = Read-StateFile
    $values[$Name] = $Value
    New-Item -ItemType Directory -Path (Split-Path $path -Parent) -Force | Out-Null
    $values.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" } | Set-Content -Path $path -Encoding utf8NoBOM
    return $true
}

function Get-EnvSetting([string]$Name) {
    $value = [Environment]::GetEnvironmentVariable($Name)
    if (-not $value -and (Test-AzdAvailable)) {
        $value = (azd env get-value $Name 2>$null | Out-String).Trim()
        if ($LASTEXITCODE -ne 0) { $value = '' }
    }
    if (-not $value) {
        $state = Read-StateFile
        if ($state.Contains($Name)) { $value = $state[$Name] }
    }
    return $value
}

function Save-EnvSetting([string]$Name, [string]$Value) {
    [Environment]::SetEnvironmentVariable($Name, $Value)
    if (Test-AzdAvailable) {
        $current = (azd env get-value $Name 2>$null | Out-String).Trim()
        if ($LASTEXITCODE -eq 0 -and $current -eq $Value) { return }
        azd env set $Name $Value 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { return }
    }
    $state = Read-StateFile
    if ($state.Contains($Name) -and $state[$Name] -eq $Value) { return }
    if (-not (Write-StateValue $Name $Value)) {
        Write-Host "  (nessun ambiente azd né AZURE_ENV_NAME) imposta nella sessione: `$env:$Name = '$Value'" -ForegroundColor Yellow
    }
}

# Command to suggest when a hook step must be resumed, depending on how the deployment is driven.
function Get-ResumeCommand([ValidateSet('postprovision', 'postdeploy')][string]$Hook) {
    if (Test-AzdAvailable) { return "azd hooks run $Hook" }
    return "./scripts/deploy_noazd.ps1 -EnvironmentName $env:AZURE_ENV_NAME -Only $Hook"
}
