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

REST Client legge `http\.env` dalla cartella del file `.http`, non l'ambiente del terminale integrato. Generalo dalla root in PowerShell 7 dopo il login Azure CLI sul tenant corretto (`az login --tenant <tenant-id>`):

```powershell
./http/New-HttpEnv.ps1                                  # ambiente azd selezionato
./http/New-HttpEnv.ps1 -EnvironmentName wpc-demo         # ambiente azd specifico
./http/New-HttpEnv.ps1 -SkipFoundryToken                 # solo demo Search (multi-fonte e ACL)
```

[New-HttpEnv.ps1](New-HttpEnv.ps1) legge endpoint e nomi KB dall'ambiente azd (oppure, senza azd o con `WPC_NO_AZD=true`, da `.deploy/<ambiente>.local.env` creato da `scripts/deploy_noazd.ps1`), ottiene i token Search (`https://search.azure.com`) e Foundry (`https://ai.azure.com`) dell'utente connesso e scrive `http\.env`. I nomi KB assenti usano i default `wpc-foundryiq-kb` e `wpc-acl-kb`.

Lo script **scrive token in chiaro nel file locale** `http\.env`, ignorato da Git: usalo solo sul tuo computer, non sincronizzarlo né condividerlo. I token non vengono stampati. Rieseguilo alla scadenza (circa un'ora) o dopo un cambio di utente; sovrascrive la configurazione locale.

Senza azd, copia [`.env.example`](.env.example) in `http\.env`, inserisci gli endpoint del deployment e i token ottenuti con Azure CLI per le stesse audience. Il token sorgente deve rappresentare un **utente delegato**, non una service principal. Non inserire mai token nei file `.http`, nei commit o negli screenshot. Anche cronologia e anteprime di REST Client possono contenere header e risposte riservate: non esportarle e pulisci la cronologia al termine. Elimina `http\.env` dopo la demo.

## Copione e interpretazione

1. **Multi-fonte:** esegui il GET per vedere `knowledgeSources`, `retrievalReasoningEffort` e `outputMode`, poi il POST. Osserva `response`, `references` e `activity`, distinguendo le fonti effettivamente interrogate da quelle configurate. La KB predefinita usa reasoning `low` e accetta `messages`; se hai una KB personalizzata `minimal`, usa il corpo `intents` della demo ACL. Questo file mostra il retrieval, non la successiva sintesi della web app. SharePoint remoto richiede sempre il token sorgente Search.
2. **ACL:** esegui i due POST senza cambiare `question`. Cambia poi la domanda nel file per provare gli accordi Direzione e ripeti entrambe le richieste. `Authorization: Bearer ...` autorizza l'accesso a Search; `x-ms-query-source-authorization` contiene il solo token utente, **senza `Bearer`**, per filtrare le sorgenti. In questo esempio locale i due token coincidono; nella web app distribuita il primo proviene dall'identità gestita. Omettere il secondo header non aggira le ACL. Zero risultati da solo non prova un diniego: verifica appartenenze, ACL e ingestion con il [notebook ACL](../notebooks/demo/demo-acl.ipynb).
3. **Agenti:** invia separatamente le tre richieste sulla stessa domanda. Confronta gli elementi di `output` (messaggi, tool, citazioni) e `usage`, quando presenti. Le chiamate sono sequenziali e indipendenti, senza contesto condiviso; non sono un benchmark né il confronto parallelo con KPI della web app. Gli agenti usano il contesto del progetto: queste richieste **non inoltrano le ACL dell'utente** alle sorgenti.

Le domande sono variabili testuali inserite nel JSON: se aggiungi virgolette o ritorni a capo, usa l'escaping JSON (`\"`, `\n`). Gli endpoint non devono terminare con `/`. Le richieste Search usano la stessa API preview della demo (`2026-11-01-preview`); gli agenti usano l'endpoint progetto `/openai/v1/responses`, senza aggiungere una versione API Search.

In caso di `401`, rinnova il token e controlla tenant/audience; con `403`, controlla i ruoli e l'accesso alla sorgente, non solo il login; con `404`, controlla endpoint e nomi di KB/agenti. Errori delle singole sorgenti possono comparire in `activity` anche con HTTP `200`; controlla inoltre `status` ed eventuali errori nella risposta degli agenti. Non ripetere tutte le chiamate dopo un `429`: attendi e verifica la quota.

Il [copione completo](../docs/demo-guide.md) e i [notebook](../notebooks/README.md) approfondiscono prerequisiti, controlli delle identità e limiti dei confronti.
