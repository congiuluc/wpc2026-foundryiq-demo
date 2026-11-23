#!/usr/bin/env pwsh
# Creates (or updates) the Foundry IQ objects on Azure AI Search:
#  - machinespecs-ks       : azureBlob knowledge source on the machinespecs container (machine manuals)
#  - ricambi-magazzino-ks  : mcpServer knowledge source on the MCP server deployed to App Service
#  - policies-sharepoint-ks: remoteSharePoint knowledge source on the policies folder (only if setup_sharepoint.ps1 ran)
#  - policies-sharepoint-indexed-ks: indexedSharePoint source with document ACLs (if an ingestion connection is configured)
#  - bing-filtered-ks      : web knowledge source restricted to configured distributor domains
#  - wpc-foundryiq-kb      : knowledge base combining blob, MCP, indexed SharePoint (if configured) and Bing;
#                            the remote SharePoint source is never added to the KB
#  - acl-docs-ks / wpc-acl-kb: ADLS Gen2 source with document-level ACLs and its demo KB (if setup_acl_demo.ps1 ran)
# and, on the Foundry project, the RemoteTool connections used by agents: knowledge base MCP endpoint (managed identity)
# and ricambi MCP server (custom keys, header x-api-key).
# Called by the azd postdeploy hook; can also be run manually after `azd up` (it reads the values from azd env).
[CmdletBinding()]
param(
    [string]$KnowledgeBaseName = 'wpc-foundryiq-kb',
    [string]$BlobKnowledgeSourceName = 'machinespecs-ks',
    [string]$McpKnowledgeSourceName = 'ricambi-magazzino-ks',
    [string]$SharePointKnowledgeSourceName = 'policies-sharepoint-ks',
    [string]$IndexedSharePointKnowledgeSourceName = 'policies-sharepoint-indexed-ks',
    [string]$BingKnowledgeSourceName = 'bing-filtered-ks',
    [string]$AclKnowledgeSourceName = 'acl-docs-ks',
    [string]$AclKnowledgeBaseName = 'wpc-acl-kb',
    [string]$KnowledgeBaseConnectionName = 'wpc-foundryiq-kb-mcp',
    [string]$McpConnectionName = 'mcp-ricambi-key',
    [string]$ApiVersion = '2026-11-01-preview'
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot 'env_settings.ps1')

function Get-OptionalSetting([string]$Name, [string]$Default = '') {
    $value = Get-EnvSetting $Name
    if (-not $value) { $value = $Default }
    return $value
}

function Get-Setting([string]$Name, [string]$Default = '') {
    $value = Get-OptionalSetting $Name $Default
    if (-not $value) { throw "Valore $Name mancante: esegui prima la fase provision (azd provision o deploy_noazd.ps1), poi ./scripts/load_env.ps1 se lanci lo script a mano." }
    return $value
}

$transientPattern = '\b(401|403|408|409|429|500|502|503|504)\b|Unauthorized|Forbidden|AuthorizationFailed|TooManyRequests|ServiceUnavailable|BadGateway|GatewayTimeout|timed out|timeout|temporarily|Error executing tool'

# Right after provision/deploy, role assignments (Search -> OpenAI/Storage, user -> Search/Foundry) take minutes
# to propagate and the MCP App Service may still be warming up: retry transient errors instead of failing postdeploy.
function Invoke-AzRestPut([string]$Label, [string[]]$AzArgs, [int]$MaxAttempts = 8, [int]$DelaySeconds = 20) {
    for ($attempt = 1; ; $attempt++) {
        $out = az @AzArgs 2>&1
        if ($LASTEXITCODE -eq 0) { return }
        $text = "$out"
        if ($attempt -ge $MaxAttempts -or $text -notmatch $transientPattern) { throw "PUT $Label fallito: $text" }
        Write-Host "  PUT $Label non riuscito (tentativo $attempt/$MaxAttempts, errore transitorio/propagazione RBAC): riprovo tra $DelaySeconds s..." -ForegroundColor Yellow
        Start-Sleep -Seconds $DelaySeconds
    }
}

function Invoke-ArmPut([string]$Url, [hashtable]$Body) {
    $tmp = Join-Path ([IO.Path]::GetTempPath()) "foundryiq-$([guid]::NewGuid().ToString('N')).local.json"
    try {
        $Body | ConvertTo-Json -Depth 20 | Set-Content -Path $tmp -Encoding utf8NoBOM
        Invoke-AzRestPut $Url @('rest', '--method', 'put', '--url', $Url, '--headers', 'Content-Type=application/json', '--body', "@$tmp", '--only-show-errors')
    }
    finally { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
}

function Invoke-SearchPut([string]$Path, [hashtable]$Body) {
    $url = "$searchEndpoint/$Path`?api-version=$ApiVersion"
    # The body can contain MCP or SharePoint credentials: keep it outside the repo and delete it afterwards.
    $tmp = Join-Path ([IO.Path]::GetTempPath()) "foundryiq-$([guid]::NewGuid().ToString('N')).local.json"
    try {
        $Body | ConvertTo-Json -Depth 20 | Set-Content -Path $tmp -Encoding utf8NoBOM
        Invoke-AzRestPut $Path @('rest', '--method', 'put', '--resource', 'https://search.azure.com', '--url', $url,
            '--headers', 'Content-Type=application/json', '--body', "@$tmp", '--only-show-errors')
    }
    finally { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
    Write-Host "  ok: $Path"
}

$searchEndpoint = (Get-Setting 'FOUNDRYIQ_DEMO_SEARCH_ENDPOINT').TrimEnd('/')
$openAIEndpoint = Get-Setting 'FOUNDRYIQ_DEMO_OPENAI_ENDPOINT'
$chatModel = @{
    kind                  = 'azureOpenAI'
    azureOpenAIParameters = @{
        resourceUri  = $openAIEndpoint
        deploymentId = Get-Setting 'FOUNDRYIQ_DEMO_CHAT_DEPLOYMENT' 'gpt-5-mini'
        modelName    = Get-Setting 'FOUNDRYIQ_DEMO_CHAT_MODEL' 'gpt-5-mini'
    }
}
$kbChatModel = @{
    kind                  = 'azureOpenAI'
    azureOpenAIParameters = @{
        resourceUri  = $openAIEndpoint
        deploymentId = Get-Setting 'FOUNDRYIQ_DEMO_GPT52_DEPLOYMENT' 'gpt-5.2'
        modelName    = 'gpt-5.2'
    }
}
$embeddingDeployment = Get-Setting 'FOUNDRYIQ_DEMO_EMBEDDING_DEPLOYMENT' 'text-embedding-3-large'
$bingAllowedDomainsSetting = Get-OptionalSetting 'FOUNDRYIQ_BING_ALLOWED_DOMAINS' 'grainger.com,mcmaster.com,rs-online.com,digikey.com,mouser.com,misumi-ec.com,automationdirect.com'
$bingAllowedDomains = @($bingAllowedDomainsSetting -split '[,;]' | ForEach-Object { $_.Trim().ToLowerInvariant() } | Where-Object { $_ })
if ($bingAllowedDomains.Count -eq 0) { throw 'FOUNDRYIQ_BING_ALLOWED_DOMAINS deve contenere almeno un dominio consentito.' }
foreach ($domain in $bingAllowedDomains) {
    if ($domain -notmatch '^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$') {
        throw "Dominio non valido in FOUNDRYIQ_BING_ALLOWED_DOMAINS: '$domain'. Usa nomi host senza schema o percorso."
    }
}

$sharePointPath = Get-OptionalSetting 'SHAREPOINT_POLICIES_URL'
$skipSharePoint = (Get-OptionalSetting 'SKIP_SHAREPOINT_SETUP') -eq 'true'
$indexedSharePointConnection = Get-OptionalSetting 'SHAREPOINT_INDEXED_CONNECTION_STRING'
$indexedSharePointQuery = ''
if ($indexedSharePointConnection -and -not $skipSharePoint) {
    $indexedSharePointQuery = Get-OptionalSetting 'SHAREPOINT_INDEXED_QUERY'
    if (-not $indexedSharePointQuery) {
        if (-not $sharePointPath) {
            throw 'Per SharePoint indexed configura SHAREPOINT_POLICIES_URL oppure SHAREPOINT_INDEXED_QUERY: non viene indicizzato tutto il sito per default.'
        }
        $indexedSharePointQuery = "includeFolder=$sharePointPath"
    }
}

Write-Host "Foundry IQ su $searchEndpoint (API $ApiVersion)"

Invoke-SearchPut "knowledgesources/$BlobKnowledgeSourceName" @{
    name                = $BlobKnowledgeSourceName
    kind                = 'azureBlob'
    description         = 'Manuali tecnici delle macchine (specifiche, componenti, manutenzione, troubleshooting).'
    azureBlobParameters = @{
        connectionString    = "ResourceId=$(Get-Setting 'FOUNDRYIQ_DEMO_STORAGE_RESOURCE_ID');"
        containerName       = Get-Setting 'FOUNDRYIQ_DEMO_BLOB_CONTAINER' 'machinespecs'
        isADLSGen2          = $false
        ingestionParameters = @{
            contentExtractionMode = 'minimal'
            embeddingModel        = @{
                kind                  = 'azureOpenAI'
                azureOpenAIParameters = @{
                    resourceUri  = $openAIEndpoint
                    deploymentId = $embeddingDeployment
                    modelName    = 'text-embedding-3-large'
                }
            }
            chatCompletionModel   = $chatModel
        }
    }
}

# The "classic RAG" agents (agents/wpc-rag-classico-*.yaml) query the index generated by the blob knowledge source
# directly with the Azure AI Search tool: read its name from createdResources (it derives from the source name).
$blobIndexName = "$BlobKnowledgeSourceName-index"
$ksJson = az rest --method get --resource 'https://search.azure.com' `
    --url "$searchEndpoint/knowledgesources/$BlobKnowledgeSourceName`?api-version=$ApiVersion" --only-show-errors 2>$null
if ($LASTEXITCODE -eq 0 -and $ksJson) {
    $created = ($ksJson | Out-String | ConvertFrom-Json -AsHashtable).azureBlobParameters.createdResources
    if ($created -and $created['index']) { $blobIndexName = $created['index'] }
}
Write-Host "  indice manuali per gli agenti RAG classico: $blobIndexName"

# Foundry IQ accepts at most 5 tools per mcpServer source: list_machine_parts is left out because
# search_parts with the machine filter covers it (agents still get it directly from the MCP server).
$mcpTools = 'search_parts', 'get_part_details', 'check_stock', 'find_alternative_parts', 'low_stock_report'
Invoke-SearchPut "knowledgesources/$McpKnowledgeSourceName" @{
    name                = $McpKnowledgeSourceName
    kind                = 'mcpServer'
    description         = 'Catalogo ricambi e giacenze di magazzino: ricerca ricambi, disponibilita, alternative, ordini aperti, ricambi sotto scorta.'
    resultsProcessing   = 'rerank'
    mcpServerParameters = @{
        serverURL      = Get-Setting 'MCP_SERVER_URL'
        authentication = @{ kind = 'storedHeaders'; storedHeadersParameters = @{ headers = @{ 'x-api-key' = (Get-Setting 'MCP_API_KEY') } } }
        tools          = @($mcpTools | ForEach-Object {
                @{
                    name              = $_
                    resultsProcessing = 'none'
                    maxOutputTokens   = 2000
                    outputParsing     = @{ kind = 'json'; jsonParameters = @{ documentsPath = '$.results[*]'; includeContext = $false } }
                }
            })
    }
}

$knowledgeSources = @(@{ name = $BlobKnowledgeSourceName }, @{ name = $McpKnowledgeSourceName })
$retrievalInstructions = @(
    'Pianifica ricerche distinte per gli aspetti della domanda e usa le sorgenti pertinenti; combina più sorgenti quando la richiesta lo richiede.'
    "Usa '$BlobKnowledgeSourceName' per manuali, specifiche delle macchine, manutenzione e troubleshooting."
    "Usa '$McpKnowledgeSourceName' per ricambi, giacenze, sottoscorta, ordini e alternative; scegli i tool pertinenti come search_parts, check_stock, low_stock_report e find_alternative_parts."
)
if ($sharePointPath -and -not $skipSharePoint) {
    # Remote source: SharePoint is queried live through the Copilot Retrieval API with the end-user token.
    Invoke-SearchPut "knowledgesources/$SharePointKnowledgeSourceName" @{
        name                       = $SharePointKnowledgeSourceName
        kind                       = 'remoteSharePoint'
        description                = 'Policy aziendali per i tecnici (sicurezza, riparare o sostituire, emergenze, comunicazione, conformita) su SharePoint.'
        remoteSharePointParameters = @{
            filterExpression = "Path:`"$sharePointPath`""
            resourceMetadata = @('Author', 'Title')
            containerTypeId  = $null
        }
    }
}
else {
    Write-Host "  SharePoint non configurato (SHAREPOINT_POLICIES_URL assente): knowledge source '$SharePointKnowledgeSourceName' saltata."
}

if ($indexedSharePointConnection -and -not $skipSharePoint) {
    Invoke-SearchPut "knowledgesources/$IndexedSharePointKnowledgeSourceName" @{
        name                       = $IndexedSharePointKnowledgeSourceName
        kind                       = 'indexedSharePoint'
        description                = 'Policy aziendali SharePoint indicizzate: sicurezza, procedure e conformita, con permessi documentali importati.'
        indexedSharePointParameters = @{
            connectionString    = $indexedSharePointConnection
            containerName       = 'useQuery'
            query               = $indexedSharePointQuery
            ingestionParameters = @{
                contentExtractionMode      = 'minimal'
                ingestionPermissionOptions = @('userIds', 'groupIds')
                embeddingModel             = @{
                    kind                  = 'azureOpenAI'
                    azureOpenAIParameters = @{
                        resourceUri  = $openAIEndpoint
                        deploymentId = $embeddingDeployment
                        modelName    = 'text-embedding-3-large'
                    }
                }
            }
        }
    }
    Write-Host '  SharePoint indexed configurato: verifica il completamento e gli eventuali errori dell''indexer nel portale Search.'
}
else {
    Write-Host "  SharePoint indexed non configurato o saltato: imposta SHAREPOINT_INDEXED_CONNECTION_STRING per aggiungere '$IndexedSharePointKnowledgeSourceName'."
}

# The KB uses only the indexed SharePoint source (with ACLs); the remote source is never added to the KB
# and is created only for direct use outside it.
if ($indexedSharePointConnection -and -not $skipSharePoint) {
    $knowledgeSources += @{ name = $IndexedSharePointKnowledgeSourceName }
    $retrievalInstructions += "Usa '$IndexedSharePointKnowledgeSourceName' per policy aziendali, sicurezza e procedure, rispettando i permessi dell'utente."
}

Invoke-SearchPut "knowledgesources/$BingKnowledgeSourceName" @{
    name          = $BingKnowledgeSourceName
    kind          = 'web'
    description   = 'Ricerca web Bing limitata ai cataloghi di distributori tecnici consentiti per informazioni aggiornate sui ricambi.'
    webParameters = @{
        domains = @{
            allowedDomains = @($bingAllowedDomains | ForEach-Object { @{ address = $_; includeSubpages = $true } })
        }
    }
}
$knowledgeSources += @{ name = $BingKnowledgeSourceName }
$retrievalInstructions += @(
    "Usa '$BingKnowledgeSourceName' per cataloghi, fornitori e informazioni pubbliche aggiornate; non includere dati riservati nelle ricerche web."
    'Mantieni esatti i nomi delle macchine e i codici dei ricambi. Non presumere disponibilità, date di consegna o autorizzazioni non presenti nelle fonti.'
)

Invoke-SearchPut "knowledgebases/$KnowledgeBaseName" @{
    name                     = $KnowledgeBaseName
    description              = 'Assistente tecnici WPC: manuali macchine, ricambi e giacenze, policy aziendali e ricerca web Bing filtrata.'
    knowledgeSources         = $knowledgeSources
    models                   = @($kbChatModel)
    # 'minimal' is not supported with MCP knowledge sources.
    retrievalReasoningEffort = @{ kind = 'low' }
    retrievalInstructions    = ($retrievalInstructions -join "`n")
    outputMode               = 'extractiveData'
    retrieveDefaults         = @{ maxRuntimeInSeconds = 60; maxOutputDocuments = 10 }
}

# ACL demo: ADLS Gen2 knowledge source that ingests the POSIX ACLs (userIds/groupIds) of each document, in a
# dedicated knowledge base so the permission trimming is easy to observe (see acl-demo.ipynb).
$aclKnowledgeBaseCreated = $false
if ((Get-OptionalSetting 'FOUNDRYIQ_ACL_GROUP_TECNICI_ID') -and (Get-OptionalSetting 'SKIP_ACL_DEMO_SETUP') -ne 'true') {
    Invoke-SearchPut "knowledgesources/$AclKnowledgeSourceName" @{
        name                = $AclKnowledgeSourceName
        kind                = 'azureBlob'
        description         = 'Documenti interni con permessi per documento (ACL ADLS Gen2): procedure pubbliche, codici service riservati ai tecnici, accordi fornitori riservati alla direzione.'
        azureBlobParameters = @{
            connectionString    = "ResourceId=$(Get-Setting 'FOUNDRYIQ_ACL_STORAGE_RESOURCE_ID');"
            containerName       = Get-Setting 'FOUNDRYIQ_ACL_CONTAINER' 'acl-docs'
            isADLSGen2          = $true
            ingestionParameters = @{
                contentExtractionMode      = 'minimal'
                ingestionPermissionOptions = @('userIds', 'groupIds')
                embeddingModel             = @{
                    kind                  = 'azureOpenAI'
                    azureOpenAIParameters = @{
                        resourceUri  = $openAIEndpoint
                        deploymentId = $embeddingDeployment
                        modelName    = 'text-embedding-3-large'
                    }
                }
            }
        }
    }
    Invoke-SearchPut "knowledgebases/$AclKnowledgeBaseName" @{
        name                     = $AclKnowledgeBaseName
        description              = 'Demo ACL Foundry IQ: ogni utente vede solo i documenti consentiti dalle ACL di origine.'
        knowledgeSources         = @(@{ name = $AclKnowledgeSourceName })
        models                   = @($chatModel)
        retrievalReasoningEffort = @{ kind = 'minimal' }
        outputMode               = 'extractiveData'
    }
    $aclKnowledgeBaseCreated = $true
}
else {
    Write-Host "  Demo ACL non configurata (FOUNDRYIQ_ACL_GROUP_TECNICI_ID assente): '$AclKnowledgeSourceName' saltata."
}

# Project connection used by Foundry agents to call the knowledge base MCP endpoint (tool knowledge_base_retrieve)
# with the project managed identity. Not expressible in Bicep yet (ProjectManagedIdentity/audience), so ARM REST.
$kbMcpEndpoint = "$searchEndpoint/knowledgebases/$KnowledgeBaseName/mcp?api-version=$ApiVersion"
$projectId = Get-Setting 'FOUNDRY_PROJECT_ID'
Invoke-ArmPut "https://management.azure.com$projectId/connections/${KnowledgeBaseConnectionName}?api-version=2025-10-01-preview" @{
    name       = $KnowledgeBaseConnectionName
    type       = 'Microsoft.MachineLearningServices/workspaces/connections'
    properties = @{
        authType      = 'ProjectManagedIdentity'
        category      = 'RemoteTool'
        target        = $kbMcpEndpoint
        isSharedToAll = $true
        audience      = 'https://search.azure.com/'
        metadata      = @{ ApiType = 'Azure' }
    }
}
Write-Host "  ok: connessione progetto Foundry '$KnowledgeBaseConnectionName'"

# Custom keys connection used by agents that call the ricambi MCP server directly (header x-api-key).
# The key lives only in the Foundry connection, never in the agent definition.
Invoke-ArmPut "https://management.azure.com$projectId/connections/${McpConnectionName}?api-version=2025-10-01-preview" @{
    name       = $McpConnectionName
    type       = 'Microsoft.MachineLearningServices/workspaces/connections'
    properties = @{
        authType      = 'CustomKeys'
        category      = 'RemoteTool'
        target        = Get-Setting 'MCP_SERVER_URL'
        isSharedToAll = $true
        credentials   = @{ keys = @{ 'x-api-key' = Get-Setting 'MCP_API_KEY' } }
        metadata      = @{ type = 'custom_MCP' }
    }
}
Write-Host "  ok: connessione progetto Foundry '$McpConnectionName'"

foreach ($pair in @(@('FOUNDRYIQ_DEMO_KB_NAME', $KnowledgeBaseName), @('FOUNDRYIQ_DEMO_KB_MCP_ENDPOINT', $kbMcpEndpoint), @('FOUNDRYIQ_DEMO_KB_CONNECTION', $KnowledgeBaseConnectionName), @('FOUNDRYIQ_MCP_CONNECTION', $McpConnectionName), @('FOUNDRYIQ_DEMO_BLOB_INDEX', $blobIndexName), @('FOUNDRYIQ_BING_ALLOWED_DOMAINS', ($bingAllowedDomains -join ',')))) {
    Save-EnvSetting $pair[0] $pair[1]
}
if ($aclKnowledgeBaseCreated) { Save-EnvSetting 'FOUNDRYIQ_ACL_KB_NAME' $AclKnowledgeBaseName }

Write-Host "Knowledge base '$KnowledgeBaseName' pronta ($($knowledgeSources.Count) sorgenti): visibile nel portale Foundry e collegabile agli agenti con la connessione '$KnowledgeBaseConnectionName' (tool MCP knowledge_base_retrieve)." -ForegroundColor Green
