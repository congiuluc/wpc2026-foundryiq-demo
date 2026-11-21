# Contribuire

Grazie per voler migliorare la demo. Apri una issue per discutere modifiche importanti prima di inviare una pull request.

## Regole

- **Solo dati sintetici:** non aggiungere documenti, nomi o dati reali di clienti o dipendenti.
- **Nessun segreto:** niente `.env`, chiavi, connection string, ID di tenant o sottoscrizione. Le esclusioni sono in [.gitignore](.gitignore); per i valori locali usa file `*.local.*`.
- **Notebook puliti:** prima del commit esegui **Jupyter: Clear All Outputs** e lascia vuoti i valori specifici dell'ambiente.
- **Infrastruttura modulare:** aggiungi le risorse come moduli in `infra/modules/` e l'automazione negli hook di `azd` descritti in [infra/README.md](infra/README.md).
- **Documentazione in italiano:** aggiorna il README della cartella che modifichi; il sito GitHub Pages viene generato dai README.

## Verifiche prima della pull request

```powershell
# Test della chat web app
cd webapp-chat
python -m pytest -q
cd ..

# Compilazione Bicep
az bicep build --file infra/main.bicep --stdout > $null

# Sito della documentazione
pip install -r docs/requirements.txt
python scripts/build_docs.py
mkdocs build --strict
```

Controlla i file in staging (`git diff --staged`) prima di ogni commit.
