# Demo con richieste HTTP

I file `.http` riproducono i tre scenari direttamente sulle API Azure, senza Python, notebook o chat web app. Usa VS Code con [REST Client](https://marketplace.visualstudio.com/items?itemName=humao.rest-client), apri un file e seleziona **Send Request** sopra la singola richiesta. I blocchi separati da `###` sono indipendenti: non eseguire tutto il file per errore.

| File | Cosa mostra |
|---|---|
| [demo-multi-fonte.http](demo-multi-fonte.http) | Configurazione KB e retrieval con `messages`, token utente per SharePoint, riferimenti e attività delle sorgenti. |
| [demo-acl.http](demo-acl.http) | Stessa domanda e stesso corpo `intents`, senza e con `x-ms-query-source-authorization`. |
| [demo-agenti.http](demo-agenti.http) | Stessa domanda ai tre agenti RAG/IQ e richiesta separata all'agente magazzino MCP tramite Responses API. |

Le chiamate usano servizi reali e possono generare costi. Non creano risorse né modificano gruppi o ACL. Il server MCP può comunque inizializzare il catalogo SQL alla prima lettura con `SQL_AUTO_SEED=true`, come descritto nella [guida MCP](../mcp-ricambi/README.md).

## Configurazione locale

Serve un ambiente già distribuito con [azd o notebook](../infra/README.md#deployment), le knowledge base indicizzate e, per la demo agenti, gli [agenti pubblicati](../agents/README.md). L'utente deve poter interrogare Search (ruolo **Search Index Data Reader**; per ispezionare la definizione KB serve anche **Search Service Contributor**) e usare il progetto Foundry (ruolo **Azure AI User**, o equivalente). Per i documenti protetti servono anche le appartenenze Entra e le ACL effettive.

REST Client legge `http\.env` dalla cartella del file `.http`, non l'ambiente del terminale integrato. Per un ambiente azd, esegui dalla root in PowerShell 7 dopo il login Azure CLI sul tenant corretto (`az login --tenant <tenant-id>`):

```powershell
$ErrorActionPreference = 'Stop'
$raw = azd env get-values --output json --no-prompt
if ($LASTEXITCODE -ne 0) { throw 'Impossibile leggere ambiente azd selezionato.' }
$values = ($raw -join "`n") | ConvertFrom-Json
foreach ($key in @('AZURE_TENANT_ID', 'FOUNDRYIQ_DEMO_SEARCH_ENDPOINT', 'FOUNDRYIQ_DEMO_PROJECT_ENDPOINT')) {
    if (-not $values.$key) { throw "Variabile azd mancante: $key" }
}

function Get-DemoToken([string]$Resource) {
    $token = az account get-access-token --tenant $values.AZURE_TENANT_ID `
        --resource $Resource --query accessToken -o tsv --only-show-errors
    if ($LASTEXITCODE -ne 0 -or -not $token) { throw "Token non disponibile per $Resource" }
    return $token.Trim()
}

$searchToken = Get-DemoToken 'https://search.azure.com'
$foundryToken = Get-DemoToken 'https://ai.azure.com'
$kbName = if ($values.FOUNDRYIQ_DEMO_KB_NAME) { $values.FOUNDRYIQ_DEMO_KB_NAME } else { 'wpc-foundryiq-kb' }
$aclKbName = if ($values.FOUNDRYIQ_ACL_KB_NAME) { $values.FOUNDRYIQ_ACL_KB_NAME } else { 'wpc-acl-kb' }
@(
    "FOUNDRYIQ_DEMO_SEARCH_ENDPOINT=$($values.FOUNDRYIQ_DEMO_SEARCH_ENDPOINT.TrimEnd('/'))"
    "FOUNDRYIQ_DEMO_KB_NAME=$kbName"
    "FOUNDRYIQ_ACL_KB_NAME=$aclKbName"
    "FOUNDRYIQ_DEMO_PROJECT_ENDPOINT=$($values.FOUNDRYIQ_DEMO_PROJECT_ENDPOINT.TrimEnd('/'))"
    "SEARCH_TOKEN=$searchToken"
    "USER_SEARCH_TOKEN=$searchToken"
    "FOUNDRY_TOKEN=$foundryToken"
) | Set-Content -LiteralPath .\http\.env -Encoding utf8
Remove-Variable searchToken, foundryToken
```

Il comando **scrive token in chiaro nel file locale** `http\.env`, ignorato da Git: usalo solo sul tuo computer, non sincronizzarlo né condividerlo. I token non vengono stampati. Ripeti il comando alla scadenza o dopo un cambio di utente; sovrascrive la configurazione locale. Per provare solo Search puoi omettere il recupero e la riga del token Foundry.

Senza azd, copia [`.env.example`](.env.example) in `http\.env`, inserisci gli endpoint del deployment e i token ottenuti con Azure CLI per le stesse audience. Il token sorgente deve rappresentare un **utente delegato**, non una service principal. Non inserire mai token nei file `.http`, nei commit o negli screenshot. Anche cronologia e anteprime di REST Client possono contenere header e risposte riservate: non esportarle e pulisci la cronologia al termine. Elimina `http\.env` dopo la demo.

## Copione e interpretazione

1. **Multi-fonte:** esegui il GET per vedere `knowledgeSources`, `retrievalReasoningEffort` e `outputMode`, poi il POST. Osserva `response`, `references` e `activity`, distinguendo le fonti effettivamente interrogate da quelle configurate. La KB predefinita usa reasoning `low` e accetta `messages`; se hai una KB personalizzata `minimal`, usa il corpo `intents` della demo ACL. Questo file mostra il retrieval, non la successiva sintesi della web app. SharePoint remoto richiede sempre il token sorgente Search.
2. **ACL:** esegui i due POST senza cambiare `question`. Cambia poi la domanda nel file per provare gli accordi Direzione e ripeti entrambe le richieste. `Authorization: Bearer ...` autorizza l'accesso a Search; `x-ms-query-source-authorization` contiene il solo token utente, **senza `Bearer`**, per filtrare le sorgenti. In questo esempio locale i due token coincidono; nella web app distribuita il primo proviene dall'identità gestita. Omettere il secondo header non aggira le ACL. Zero risultati da solo non prova un diniego: verifica appartenenze, ACL e ingestion con il [notebook ACL](../notebooks/demo/demo-acl.ipynb).
3. **Agenti:** invia separatamente le tre richieste sulla stessa domanda. Confronta gli elementi di `output` (messaggi, tool, citazioni) e `usage`, quando presenti. Le chiamate sono sequenziali e indipendenti, senza contesto condiviso; non sono un benchmark né il confronto parallelo con KPI della web app. Gli agenti usano il contesto del progetto: queste richieste **non inoltrano le ACL dell'utente** alle sorgenti.

Le domande sono variabili testuali inserite nel JSON: se aggiungi virgolette o ritorni a capo, usa l'escaping JSON (`\"`, `\n`). Gli endpoint non devono terminare con `/`. Le richieste Search usano la stessa API preview della demo (`2026-11-01-preview`); gli agenti usano l'endpoint progetto `/openai/v1/responses`, senza aggiungere una versione API Search.

In caso di `401`, rinnova il token e controlla tenant/audience; con `403`, controlla i ruoli e l'accesso alla sorgente, non solo il login; con `404`, controlla endpoint e nomi di KB/agenti. Errori delle singole sorgenti possono comparire in `activity` anche con HTTP `200`; controlla inoltre `status` ed eventuali errori nella risposta degli agenti. Non ripetere tutte le chiamate dopo un `429`: attendi e verifica la quota.

Il [copione completo](../docs/demo-guide.md) e i [notebook](../notebooks/README.md) approfondiscono prerequisiti, controlli delle identità e limiti dei confronti.
