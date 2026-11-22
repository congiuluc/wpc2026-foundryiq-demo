#!/usr/bin/env pwsh
# Prepares the SharePoint content for the remote and indexed SharePoint knowledge sources of Foundry IQ:
#  1. signs in to Microsoft Graph (delegated, same tenant as Azure: required by the remote SharePoint source)
#  2. finds the demo site or creates it as a communication site if it does not exist
#  3. uploads data/sharepoint/policies/{EN,IT} to matching language folders (skips unchanged files)
#  4. saves SHAREPOINT_SITE_URL, SHAREPOINT_SITE_ID and SHAREPOINT_POLICIES_URL in the azd environment
# Called by the azd postprovision hook; can also be run manually: pwsh ./scripts/setup_sharepoint.ps1
# Optional azd env values: SHAREPOINT_SITE_URL (existing or new site), SHAREPOINT_FOLDER (default 'policies'),
# SHAREPOINT_USE_DEVICE_CODE=true (sign-in with device code, e.g. in a remote shell),
# SHAREPOINT_GRAPH_AUTH=rest (skip the Microsoft.Graph module and sign in via OAuth + REST; used automatically
# when the module cannot be installed, e.g. PowerShell Gallery blocked by a proxy), SHAREPOINT_GRAPH_CLIENT_ID.
[CmdletBinding()]
param(
    [string]$SiteAlias = 'wpc-foundry-iq-demo',
    [string]$SiteTitle = 'WPC Foundry IQ Demo',
    [string]$SourceFolder = (Join-Path (Split-Path -Parent $PSScriptRoot) 'data/sharepoint/policies')
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot 'env_settings.ps1')

function Get-Setting([string]$Name, [string]$Default = '') {
    $value = Get-EnvSetting $Name
    if (-not $value) { $value = $Default }
    return $value
}

function Set-AzdValue([string]$Name, [string]$Value) { Save-EnvSetting $Name $Value }

function Invoke-Graph([string]$Method, [string]$Uri, $Body = $null) {
    if (-not $script:UseRestGraph) {
        $params = @{ Method = $Method; Uri = $Uri; OutputType = 'HashTable' }
        if ($null -ne $Body) { $params.Body = ($Body | ConvertTo-Json -Depth 10); $params.ContentType = 'application/json' }
        return Invoke-MgGraphRequest @params
    }
    $params = @{ Method = $Method; Uri = "https://graph.microsoft.com/$Uri"; Headers = @{ Authorization = "Bearer $($script:GraphToken)" } }
    if ($null -ne $Body) { $params.Body = ($Body | ConvertTo-Json -Depth 10); $params.ContentType = 'application/json' }
    $response = Invoke-WebRequest @params
    if ($response.Content) { return ($response.Content | ConvertFrom-Json -AsHashtable) }
}

function Send-GraphFile([string]$Uri, [string]$Path) {
    if (-not $script:UseRestGraph) {
        Invoke-MgGraphRequest -Method PUT -Uri $Uri -InputFilePath $Path -ContentType 'application/octet-stream' | Out-Null
        return
    }
    Invoke-WebRequest -Method PUT -Uri "https://graph.microsoft.com/$Uri" -InFile $Path -ContentType 'application/octet-stream' `
        -Headers @{ Authorization = "Bearer $($script:GraphToken)" } | Out-Null
}

function Get-GraphOrNull([string]$Uri) {
    try { return Invoke-Graph GET $Uri }
    catch {
        if ("$($_.Exception.Message) $($_.ErrorDetails)" -match 'NotFound|404|itemNotFound') { return $null }
        throw
    }
}

function Get-TokenClaims([string]$Jwt) {
    $payload = $Jwt.Split('.')[1].Replace('-', '+').Replace('_', '/')
    $payload += '=' * ((4 - $payload.Length % 4) % 4)
    return [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($payload)) | ConvertFrom-Json -AsHashtable
}

function Get-RestGraphToken([string[]]$Scopes) {
    # Delegated sign-in without the Microsoft.Graph module (e.g. when PowerShell Gallery is blocked).
    # Default client: "Microsoft Graph Command Line Tools", the same public app used by Connect-MgGraph.
    $clientId = Get-Setting 'SHAREPOINT_GRAPH_CLIENT_ID' '14d82eec-204b-4c2f-b7e8-296a70dab67e'
    $authority = "https://login.microsoftonline.com/$tenantId/oauth2/v2.0"
    $scope = (@($Scopes | ForEach-Object { "https://graph.microsoft.com/$_" }) + 'offline_access') -join ' '

    if ((Get-Setting 'SHAREPOINT_USE_DEVICE_CODE') -eq 'true') {
        $device = Invoke-RestMethod -Method POST -Uri "$authority/devicecode" -Body @{ client_id = $clientId; scope = $scope }
        Write-Host $device.message -ForegroundColor Yellow
        $deadline = (Get-Date).AddSeconds([int]$device.expires_in)
        while ((Get-Date) -lt $deadline) {
            Start-Sleep -Seconds ([int]$device.interval)
            try {
                return (Invoke-RestMethod -Method POST -Uri "$authority/token" -Body @{
                        client_id = $clientId; grant_type = 'urn:ietf:params:oauth:grant-type:device_code'; device_code = $device.device_code
                    }).access_token
            } catch {
                if ("$($_.ErrorDetails)" -notmatch 'authorization_pending|slow_down') { throw }
            }
        }
        throw 'Accesso con device code scaduto.'
    }

    # Authorization code + PKCE with a loopback redirect (http://localhost, any port, is registered for this client).
    $tcp = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0); $tcp.Start()
    $port = $tcp.LocalEndpoint.Port; $tcp.Stop()
    $redirect = "http://localhost:$port/"
    $bytes = [byte[]]::new(32); [Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    $verifier = [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
    $challenge = [Convert]::ToBase64String([Security.Cryptography.SHA256]::HashData([Text.Encoding]::ASCII.GetBytes($verifier))).TrimEnd('=').Replace('+', '-').Replace('/', '_')
    $state = [guid]::NewGuid().ToString('N')
    $query = @{
        client_id = $clientId; response_type = 'code'; redirect_uri = $redirect; scope = $scope; state = $state
        code_challenge = $challenge; code_challenge_method = 'S256'; prompt = 'select_account'
    }.GetEnumerator() | ForEach-Object { "$($_.Key)=$([Uri]::EscapeDataString($_.Value))" }
    $authorizeUrl = "$authority/authorize?$($query -join '&')"

    $listener = [Net.HttpListener]::new(); $listener.Prefixes.Add($redirect); $listener.Start()
    try {
        Write-Host "Apro il browser per l'accesso a Microsoft Graph. Se non si apre, visita:`n$authorizeUrl" -ForegroundColor Yellow
        try { Start-Process $authorizeUrl } catch { }
        $pending = $listener.GetContextAsync()
        if (-not $pending.Wait([TimeSpan]::FromMinutes(5))) { throw 'Accesso non completato entro 5 minuti.' }
        $context = $pending.Result
        $params = [Web.HttpUtility]::ParseQueryString($context.Request.Url.Query)
        $html = [Text.Encoding]::UTF8.GetBytes('<html><body style="font-family:Segoe UI">Accesso completato: puoi chiudere questa finestra.</body></html>')
        $context.Response.ContentType = 'text/html; charset=utf-8'
        $context.Response.OutputStream.Write($html, 0, $html.Length); $context.Response.Close()
    } finally { $listener.Stop() }
    if ($params['error']) { throw "Accesso non riuscito: $($params['error']) $($params['error_description'])" }
    if ($params['state'] -ne $state) { throw 'Risposta di accesso non valida (state diverso).' }
    return (Invoke-RestMethod -Method POST -Uri "$authority/token" -Body @{
            client_id = $clientId; grant_type = 'authorization_code'; code = $params['code']
            redirect_uri = $redirect; code_verifier = $verifier; scope = $scope
        }).access_token
}

function Connect-Graph([string[]]$Scopes) {
    if ($script:UseRestGraph) {
        if ($script:GraphToken) {
            $claims = Get-TokenClaims $script:GraphToken
            $granted = "$($claims['scp'])" -split ' '
            if ($claims['tid'] -eq $tenantId -and -not ($Scopes | Where-Object { $granted -notcontains $_ })) { return }
        }
        $script:GraphToken = Get-RestGraphToken $Scopes
        return
    }
    $ctx = Get-MgContext
    if ($ctx -and $ctx.TenantId -eq $tenantId -and -not ($Scopes | Where-Object { $ctx.Scopes -notcontains $_ })) { return }
    $connect = @{ TenantId = $tenantId; Scopes = $Scopes; NoWelcome = $true }
    if ((Get-Setting 'SHAREPOINT_USE_DEVICE_CODE') -eq 'true') { $connect.UseDeviceCode = $true }
    Connect-MgGraph @connect
}

$filesByLanguage = @{}
foreach ($language in @('EN', 'IT')) {
    $languageFolder = Join-Path $SourceFolder $language
    if (-not (Test-Path -LiteralPath $languageFolder -PathType Container)) { throw "Cartella sorgente non trovata: $languageFolder" }
    $filesByLanguage[$language] = @(Get-ChildItem -LiteralPath $languageFolder -File -Filter '*.pdf')
    if ($filesByLanguage[$language].Count -eq 0) { throw "Nessun PDF da caricare in $languageFolder" }
}

$script:UseRestGraph = (Get-Setting 'SHAREPOINT_GRAPH_AUTH') -eq 'rest'
$script:GraphToken = $null
if (-not $script:UseRestGraph -and -not (Get-Module -ListAvailable -Name Microsoft.Graph.Authentication)) {
    Write-Host 'Installo il modulo Microsoft.Graph.Authentication (CurrentUser)...'
    try {
        if (Get-Command Install-PSResource -ErrorAction SilentlyContinue) {
            if (-not (Get-PSResourceRepository -Name PSGallery -ErrorAction SilentlyContinue)) {
                Register-PSResourceRepository -PSGallery -Trusted
            }
            Install-PSResource Microsoft.Graph.Authentication -Scope CurrentUser -Repository PSGallery -TrustRepository -Quiet
        } else {
            if (-not (Get-PSRepository -Name PSGallery -ErrorAction SilentlyContinue)) {
                Register-PSRepository -Default
            }
            Install-Module Microsoft.Graph.Authentication -Scope CurrentUser -Force -AllowClobber -Repository PSGallery
        }
    } catch {
        Write-Warning "Modulo non installabile ($($_.Exception.Message.Split([Environment]::NewLine)[0])). Uso Microsoft Graph via REST senza modulo."
        $script:UseRestGraph = $true
    }
}
if (-not $script:UseRestGraph) { Import-Module Microsoft.Graph.Authentication }

# Remote SharePoint knowledge sources do not support cross-tenant access: sign in to the Azure tenant.
$tenantId = Get-Setting 'AZURE_TENANT_ID'
if (-not $tenantId) { throw "AZURE_TENANT_ID mancante: esegui prima 'azd provision'." }
$siteUrl = Get-Setting 'SHAREPOINT_SITE_URL'
# Creating the site needs Sites.Create.All: request it upfront unless an existing site is provided.
$graphScopes = if ($siteUrl) { @('Sites.ReadWrite.All') } else { @('Sites.ReadWrite.All', 'Sites.Create.All') }
Connect-Graph $graphScopes
$account = if ($script:UseRestGraph) { $c = Get-TokenClaims $script:GraphToken; if ($c['upn']) { $c['upn'] } else { $c['unique_name'] } } else { (Get-MgContext).Account }
Write-Host "Microsoft Graph: $account (tenant $tenantId, scope: $($graphScopes -join ', '))"

if (-not $siteUrl) {
    $rootSite = Invoke-Graph GET 'v1.0/sites/root?$select=siteCollection'
    $siteUrl = "https://$($rootSite.siteCollection.hostname)/sites/$SiteAlias"
}
$siteUri = [Uri]$siteUrl.TrimEnd('/')
$siteLookup = "v1.0/sites/$($siteUri.Host):$($siteUri.AbsolutePath)?`$select=id,webUrl,displayName"

$site = Get-GraphOrNull $siteLookup
if (-not $site) {
    Write-Host "Il sito $siteUri non esiste: lo creo (communication site)..."
    Connect-Graph @('Sites.ReadWrite.All', 'Sites.Create.All')
    # Site creation is only available in the Graph beta endpoint.
    Invoke-Graph POST 'beta/sites' @{
        name                = $SiteTitle
        webUrl              = $siteUri.AbsoluteUri
        description         = 'Documenti per la demo Foundry IQ (remote SharePoint knowledge source).'
        locale              = 'en-US'
        shareByEmailEnabled = $false
        template            = 'sitepagepublishing'
    } | Out-Null
    for ($i = 1; $i -le 30 -and -not $site; $i++) {
        Start-Sleep -Seconds 10
        $site = Get-GraphOrNull $siteLookup
        if (-not $site) { Write-Host "  provisioning del sito in corso ($i/30)..." }
    }
    if (-not $site) { throw "Il sito $siteUri non e' ancora disponibile: riprova tra qualche minuto con pwsh ./scripts/setup_sharepoint.ps1" }
}
Write-Host "Sito SharePoint: $($site.webUrl)"

$folder = Get-Setting 'SHAREPOINT_FOLDER' 'policies'
$drive = Invoke-Graph GET "v1.0/sites/$($site.id)/drive?`$select=id,name,webUrl"
$folderPath = [Uri]::EscapeDataString($folder)
$folderItem = Get-GraphOrNull "v1.0/drives/$($drive.id)/root:/${folderPath}?`$select=id,folder,webUrl"
if (-not $folderItem) {
    $folderItem = Invoke-Graph POST "v1.0/drives/$($drive.id)/root/children" @{
        name = $folder; folder = @{}; '@microsoft.graph.conflictBehavior' = 'fail'
    }
}
if (-not $folderItem.ContainsKey('folder')) { throw "Il percorso SharePoint '$folder' non e' una cartella." }

$uploaded = 0
$total = 0
foreach ($language in @('EN', 'IT')) {
    $languageItem = Get-GraphOrNull "v1.0/drives/$($drive.id)/items/$($folderItem.id):/${language}?`$select=id,folder"
    if (-not $languageItem) {
        $languageItem = Invoke-Graph POST "v1.0/drives/$($drive.id)/items/$($folderItem.id)/children" @{
            name = $language; folder = @{}; '@microsoft.graph.conflictBehavior' = 'fail'
        }
    }
    if (-not $languageItem.ContainsKey('folder')) { throw "Il percorso SharePoint '$folder/$language' non e' una cartella." }
    $existing = @{}
    $current = Invoke-Graph GET "v1.0/drives/$($drive.id)/items/$($languageItem.id)/children?`$select=name,size&`$top=999"
    foreach ($item in $current.value) { $existing[$item.name] = [long]$item.size }
    foreach ($file in $filesByLanguage[$language]) {
        $total++
        if ($existing.ContainsKey($file.Name) -and $existing[$file.Name] -eq $file.Length) { continue }
        $target = "v1.0/drives/$($drive.id)/items/$($languageItem.id):/$([Uri]::EscapeDataString($file.Name)):/content"
        Send-GraphFile $target $file.FullName
        $uploaded++
        Write-Host "  caricato: $language/$($file.Name)"
    }
}
Write-Host "File caricati: $uploaded, gia' aggiornati: $($total - $uploaded) (libreria '$($drive.name)', cartella '$folder')."

Set-AzdValue 'SHAREPOINT_SITE_URL' $site.webUrl
# Graph site id is "<hostname>,<site collection id>,<web id>": the KQL SiteID filter uses the site collection id.
Set-AzdValue 'SHAREPOINT_SITE_ID' ($site.id -split ',')[1]
Set-AzdValue 'SHAREPOINT_POLICIES_URL' ([Uri]::UnescapeDataString($folderItem.webUrl))
Write-Host "Cartella documenti: $([Uri]::UnescapeDataString($folderItem.webUrl))" -ForegroundColor Green
