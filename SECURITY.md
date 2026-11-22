# Sicurezza

## Segnalare una vulnerabilità

Non aprire issue pubbliche per problemi di sicurezza. Usa **Security > Report a vulnerability** del repository ([segnalazione privata GitHub](https://docs.github.com/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)) e includi:

- il componente coinvolto (`infra`, `mcp-ricambi`, `webapp-chat`, `agents`, notebook);
- i passaggi per riprodurre il problema;
- l'impatto previsto.

## Ambito

Questo repository è una demo con dati sintetici e non è destinato alla produzione. Prima di riutilizzarlo in un ambiente reale, rivedi in particolare:

- l'autenticazione del server MCP e della chat web app;
- le assegnazioni dei ruoli Azure create da `azd`;
- l'accesso di rete pubblico alle risorse (App Service, Azure SQL, Azure AI Search, Storage).

## Segreti

Il repository non deve contenere credenziali, connection string, ID di tenant o sottoscrizione né output dei notebook. Se ne trovi uno, segnalalo in privato: chi gestisce il repository ruoterà la credenziale e la rimuoverà dalla cronologia.

## Screenshot, video e trace

Gli asset in `docs/images` e `docs/videos` sono pubblicati anche su GitHub Pages. Usa soltanto il corpus sintetico della demo: una risposta di un servizio reale può comunque contenere dati non adatti alla pubblicazione. Non registrare login, device code, console con variabili azd, header HTTP, token o impostazioni del portale.

Il [companion script di acquisizione](scripts/capture_demo_media.py) oscura nome, account e identificatori nel pannello del profilo e salva nel manifest solo metriche e riepiloghi delle source, non i corpi completi delle risposte. Questo non sostituisce la revisione visiva: controlla tutti i fotogrammi e gli screenshot prima del commit, inclusi riferimenti, URL e dettagli della Trace. Non aggiungere profili browser, cookie o registrazioni temporanee al repository.
