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
