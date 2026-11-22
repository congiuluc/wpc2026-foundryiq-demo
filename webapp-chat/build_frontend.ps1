$ErrorActionPreference = 'Stop'
Push-Location (Join-Path $PSScriptRoot 'frontend')
try {
    npm ci
    if ($LASTEXITCODE -ne 0) { throw "npm ci fallito ($LASTEXITCODE)" }
    npm run build
    if ($LASTEXITCODE -ne 0) { throw "Build frontend fallita ($LASTEXITCODE)" }
}
finally { Pop-Location }
