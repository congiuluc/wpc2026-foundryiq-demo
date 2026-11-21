# Foundry IQ e SharePoint Remote Knowledge Source

## Obiettivo

Questa nota riassume i prerequisiti e i passaggi operativi per guidare un cliente nel collegamento di **SharePoint come remote knowledge source in Foundry IQ**.

La funzionalita e in **preview**: non ha SLA, non e consigliata per workload di produzione e puo avere limitazioni o cambiamenti.

## Automazione nella demo

Con `azd up` gli hook automatizzano i passaggi tecnici: `postprovision` ([setup_sharepoint.ps1](../../scripts/setup_sharepoint.ps1)) crea il sito `wpc-foundryiq-demo` se non esiste e carica i PDF in `policies`; `postdeploy` ([setup_foundry_iq.ps1](../../scripts/setup_foundry_iq.ps1)) crea la knowledge source `policies-sharepoint-ks`, la aggiunge alla knowledge base e crea la connessione `RemoteTool` del progetto Foundry. Identita gestita del progetto e ruolo `Search Index Data Reader` sono gia nel Bicep. Restano da verificare licenze/PAYG e l'eventuale consenso amministrativo Microsoft Graph.

Il setup SharePoint e opzionale e puo terminare con un avviso senza bloccare il deployment. Se PowerShell Gallery e bloccata, lo script tenta l'autenticazione Graph via REST; per forzarla con azd usa `azd env set SHAREPOINT_GRAPH_AUTH rest`, riesegui `pwsh ./scripts/setup_sharepoint.ps1` e poi `azd hooks run postdeploy`. Per la ripresa senza azd e gli altri casi vedi [Ripresa dopo un errore](../../infra/README.md#ripresa-dopo-un-errore).

## Architettura in breve

Una **remote SharePoint knowledge source** in Foundry IQ usa Azure AI Search / agentic retrieval per interrogare SharePoint tramite la **Microsoft 365 Copilot Retrieval API**.

Caratteristiche principali:

- i contenuti SharePoint vengono interrogati live a runtime;
- non viene creato un indice Azure AI Search con i documenti SharePoint;
- SharePoint resta la sorgente dati autorevole;
- le autorizzazioni SharePoint vengono rispettate tramite identita utente a runtime;
- le risposte possono includere riferimenti/citazioni alle sorgenti.

## Prerequisiti principali

| Area | Prerequisito |
|---|---|
| Tenant | SharePoint/Microsoft 365 e Azure devono essere nello stesso Microsoft Entra tenant. Cross-tenant non supportato. |
| Licensing | Ogni utente che interroga SharePoint deve avere una licenza Microsoft 365 Copilot oppure il Retrieval API pay-as-you-go abilitato. |
| SharePoint | Gli utenti finali devono avere accesso almeno in lettura ai siti/documenti SharePoint interrogati. |
| Azure AI Search | Serve un servizio Azure AI Search in una regione che supporta agentic retrieval / Foundry IQ. |
| Foundry | Serve un Microsoft Foundry project, non hub-based, con un model deployment disponibile, ad esempio `gpt-4.1-mini`. |
| Identita gestita | Il progetto Foundry deve avere una system-assigned managed identity per interagire con Azure AI Search. |
| RBAC Search | La managed identity del progetto Foundry deve avere almeno `Search Index Data Reader` sul servizio Azure AI Search. |
| Connessione MCP | Va creata una project connection Foundry di tipo `RemoteTool` verso l'endpoint MCP della knowledge base. |

## Licensing: Copilot o Pay-as-you-go

Per ogni utente che interroga contenuti SharePoint serve una delle due opzioni.

### Opzione 1: licenza Microsoft 365 Copilot

Se l'utente ha una licenza **Microsoft 365 Copilot**, puo usare la Retrieval API inclusa nella licenza.

Per verificarlo nel Microsoft 365 admin center:

1. Vai su **Users** > **Active users**.
2. Apri l'utente.
3. Vai su **Licenses and apps**.
4. Verifica che **Microsoft 365 Copilot** sia selezionata.

Microsoft consiglia anche il diagnostic **Copilot License Details** per verificare se uno specifico utente soddisfa i requisiti Copilot:

https://aka.ms/CopilotLicenseDetails

### Opzione 2: Retrieval API pay-as-you-go

Se l'utente non ha licenza Microsoft 365 Copilot, puo usare la Retrieval API tramite **pay-as-you-go**.

Prerequisiti per abilitare PAYG:

- accesso admin a Microsoft 365;
- Azure subscription valida/in good standing;
- resource group Azure;
- ruolo **Owner** o **Contributor** sulla subscription Azure;
- almeno una licenza Microsoft 365 Copilot presente nel tenant durante l'abilitazione e l'uso del servizio PAYG.

Per configurarlo nel Microsoft 365 admin center:

1. Vai su **Copilot** > **Billing & usage** > **Pay-as-you-go** > **Microsoft 365 Copilot Retrieval API**.
2. Crea una nuova billing policy.
3. Seleziona subscription Azure, resource group e regione.
4. Scegli gli utenti o gruppi coperti dalla policy.
5. Configura eventuale budget.
6. Completa la review.

Nota: dopo l'abilitazione possono servire circa **2 ore** di propagazione. La prima chiamata dopo la propagazione puo ancora fallire mentre la billing policy viene aggiornata; dalla chiamata successiva dovrebbe funzionare.

## Dove verificare nel tenant

Nel Microsoft 365 admin center, la sezione **Copilot > Usage** mostra l'adozione e il numero di licenze assegnate, ma non e il punto principale per configurare PAYG.

Per PAYG bisogna usare invece:

**Copilot > Billing & usage > Pay-as-you-go > Microsoft 365 Copilot Retrieval API**

Se la voce o il servizio **Microsoft 365 Copilot Retrieval API** non compare, verificare:

- ruolo dell'utente amministratore, idealmente Global admin o Billing admin;
- presenza di almeno una licenza Microsoft 365 Copilot nel tenant;
- disponibilita di una Azure subscription valida;
- permessi Owner o Contributor sulla subscription;
- disponibilita della feature preview nel tenant;
- eventuali limitazioni del tenant demo/lab.

## System-assigned managed identity del progetto Foundry

Il progetto Foundry deve avere una **system-assigned managed identity** per chiamare Azure AI Search in modo sicuro.

### Configurazione da Azure Portal

1. Vai su **Azure Portal**.
2. Apri il resource group dove si trova il progetto Foundry.
3. Apri la risorsa del **Microsoft Foundry project**.
   - Il tipo risorsa e simile a `Microsoft.CognitiveServices/accounts/projects`.
   - Attenzione: va selezionato il progetto, non solo il Foundry account / AI Services parent.
4. Vai su **Identity**.
5. Nella tab **System assigned**, imposta **Status = On**.
6. Clicca **Save** e conferma.
7. Copia l'**Object ID** o il **Principal ID** della managed identity.

### Assegnazione ruolo su Azure AI Search

1. Apri la risorsa **Azure AI Search**.
2. Vai su **Access control (IAM)**.
3. Clicca **Add** > **Add role assignment**.
4. Seleziona:
   - **Search Index Data Reader** per retrieval read-only;
   - **Search Index Data Contributor** solo se l'agente deve scrivere documenti negli indici.
5. In **Members**, scegli **Managed identity**.
6. Seleziona la managed identity del progetto Foundry.
7. Clicca **Review + assign**.

Dopo l'assegnazione, attendere qualche minuto per la propagazione RBAC.

### Configurazione via Azure CLI

```powershell
$projectResourceId = "/subscriptions/<sub-id>/resourceGroups/<rg>/providers/Microsoft.CognitiveServices/accounts/<foundry-account>/projects/<project-name>"
$searchResourceId = "/subscriptions/<sub-id>/resourceGroups/<rg>/providers/Microsoft.Search/searchServices/<search-service-name>"

az resource update `
  --ids $projectResourceId `
  --set identity.type=SystemAssigned

$principalId = az resource show `
  --ids $projectResourceId `
  --query identity.principalId `
  -o tsv

az role assignment create `
  --assignee-object-id $principalId `
  --assignee-principal-type ServicePrincipal `
  --role "Search Index Data Reader" `
  --scope $searchResourceId
```

Verifica:

```powershell
az role assignment list `
  --assignee $principalId `
  --scope $searchResourceId `
  -o table
```

## Creazione della SharePoint remote knowledge source

La knowledge source SharePoint remota e un oggetto di Azure AI Search / Foundry IQ.

Parametri principali:

- `kind`: `remoteSharePoint`
- `filterExpression`: opzionale, per limitare lo scope SharePoint
- `resourceMetadata`: opzionale, per richiedere metadati come `Author` e `Title`

Esempio concettuale:

```json
{
  "name": "my-sharepoint-ks",
  "kind": "remoteSharePoint",
  "description": "Knowledge source SharePoint remota",
  "remoteSharePointParameters": {
    "filterExpression": "FileExtension:\"docx\" OR FileExtension:\"pdf\"",
    "resourceMetadata": [ "Author", "Title" ]
  }
}
```

Esempi di filtri utili:

| Obiettivo | Esempio |
|---|---|
| Limitare a un sito | `SiteID:"<site-id>"` |
| Limitare a un path | `Path:"https://contoso.sharepoint.com/sites/site/Shared Documents/folder"` |
| Limitare a file type | `FileExtension:"docx" OR FileExtension:"pdf" OR FileExtension:"pptx"` |
| Limitare per data modifica | `LastModifiedTime >= 2024-07-22 AND LastModifiedTime <= 2025-01-08` |
| Limitare per sensitivity label | `InformationProtectionLabelId:"<label-id>"` |

## Collegamento della knowledge base all'agente Foundry

Per usare la knowledge base da un agente Foundry, si crea una project connection di tipo **RemoteTool** verso l'endpoint MCP della knowledge base:

```text
https://<search-service>.search.windows.net/knowledgebases/<knowledge-base-name>/mcp?api-version=2026-11-01-preview
```

La connection usa:

- `authType`: `ProjectManagedIdentity`
- `category`: `RemoteTool`
- `audience`: `https://search.azure.com/`

L'agente poi usa il tool MCP `knowledge_base_retrieve`.

## Permission trimming e test consigliato

Per SharePoint remote knowledge source, le permission vengono applicate a query time. Il token dell'utente finale viene passato tramite header:

```text
x-ms-query-source-authorization: <user-access-token>
```

Test consigliato davanti al cliente:

| Utente | Licenza/PAYG | Accesso SharePoint | Risultato atteso |
|---|---|---|---|
| User A | Copilot license o PAYG | Ha accesso al documento | Retrieval restituisce contenuto e citazioni |
| User B | Copilot license o PAYG | Non ha accesso al documento | Retrieval non restituisce quel contenuto |
| User C | Nessuna Copilot license e niente PAYG | Accesso al documento | Errore licenza o retrieval fallita |

Questo test valida sia il licensing sia il permission trimming SharePoint.

## Matrice rapida troubleshooting

| Sintomo | Causa probabile | Controllo o soluzione |
|---|---|---|
| `403 - Authorization Failed: User does not have valid license` | Licenza per la Copilot Retrieval API mancante | Assegnare una licenza Microsoft 365 Copilot all'utente oppure abilitare Retrieval API pay-as-you-go |
| `AuthenticationError: AppOnly OBO tokens not supported by target service` | E stato usato un token applicativo invece del token delegato dell'utente | Implementare il flusso On-Behalf-Of e passare il token utente in `x-ms-query-source-authorization` |
| `401` o authentication failure | Token non valido, audience errata o accesso cross-tenant | Verificare che Azure, Foundry, Microsoft 365 e l'utente appartengano allo stesso tenant; richiedere il token per `https://search.azure.com/.default` |
| Zero risultati senza errori e SharePoint non compare nelle activity | Contesto utente non propagato oppure knowledge source esclusa dal query planner | Controllare la presenza di `x-ms-query-source-authorization` e verificare le activity del retrieval |
| SharePoint viene interrogato ma non restituisce documenti | L'utente non dispone dei permessi SharePoint necessari | Accedere a SharePoint con lo stesso account e verificare l'apertura del sito, della document library e del documento |
| `403` restituito da Azure AI Search | Ruoli RBAC Azure mancanti | Verificare `Search Index Data Reader` per la managed identity del progetto e `Search Service Contributor` per chi configura la knowledge source |
| Retrieval lento o timeout | Scope SharePoint troppo ampio o capacita AI Search insufficiente | Limitare siti, library o file tramite `filterExpression`, usare query piu specifiche e valutare l'aumento delle repliche |
| Nessun risultato per domande come "dove si trova il file?" | La query cerca metadati di posizione invece del contenuto | Formulare una domanda sul contenuto oppure applicare un `filterExpression` su path, sito o data |

## Limitazioni da comunicare al cliente

- Feature in preview, senza SLA.
- Solo SharePoint per questa remote knowledge source.
- Non supporta OneDrive o Copilot connectors nella remote SharePoint knowledge source.
- Limite di **200 richieste per utente/ora**.
- Query max **1.500 caratteri**.
- Massimo **25 risultati** per query.
- Hybrid/semantic retrieval supportata solo per `.doc`, `.docx`, `.pptx`, `.pdf`, `.aspx`, `.one`.
- Non supporta contenuti non testuali come immagini e grafici.
- I risultati della Retrieval API sono non ordinati.
- KQL non valido puo essere ignorato, quindi i filtri vanno testati.

## Checklist per la sessione cliente

1. Confermare che Azure e Microsoft 365 siano nello stesso Entra tenant.
2. Confermare chi usera la soluzione e se ha licenza Microsoft 365 Copilot.
3. Se necessario, configurare PAYG in **Copilot > Billing & usage > Pay-as-you-go > Microsoft 365 Copilot Retrieval API**.
4. Verificare Azure AI Search in regione supportata.
5. Verificare Microsoft Foundry project e model deployment.
6. Abilitare system-assigned managed identity sul progetto Foundry.
7. Assegnare `Search Index Data Reader` alla managed identity sul servizio Azure AI Search.
8. Creare la remote SharePoint knowledge source.
9. Aggiungere la knowledge source a una knowledge base.
10. Creare la RemoteTool connection dal progetto Foundry alla knowledge base MCP.
11. Collegare l'agente Foundry al tool MCP.
12. Testare con almeno due utenti con permessi SharePoint diversi.

## Riferimenti

- SharePoint remote knowledge source: https://learn.microsoft.com/azure/search/agentic-knowledge-source-how-to-sharepoint-remote
- Connect Foundry IQ knowledge base to agents: https://learn.microsoft.com/azure/foundry/agents/how-to/foundry-iq-connect
- Microsoft 365 Copilot Retrieval API: https://learn.microsoft.com/microsoft-365/copilot/extensibility/api/ai-services/retrieval/overview
- Retrieval API pay-as-you-go: https://learn.microsoft.com/microsoft-365/copilot/extensibility/api/ai-services/retrieval/paygo-retrieval
- Microsoft 365 Copilot licensing: https://learn.microsoft.com/microsoft-365/copilot/microsoft-365-copilot-licensing
