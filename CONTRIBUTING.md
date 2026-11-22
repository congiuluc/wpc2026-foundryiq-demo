# Contribuire

Grazie per voler migliorare la demo. Apri una issue per discutere modifiche importanti prima di inviare una pull request.

## Regole

- **Solo dati sintetici:** non aggiungere documenti, nomi o dati reali di clienti o dipendenti.
- **Nessun segreto:** niente `.env`, chiavi, connection string, ID di tenant o sottoscrizione. Le esclusioni sono in [.gitignore](.gitignore); per i valori locali usa file `*.local.*`.
- **Notebook puliti:** prima del commit esegui **Jupyter: Clear All Outputs** e lascia vuoti i valori specifici dell'ambiente.
- **Infrastruttura modulare:** aggiungi le risorse come moduli in `infra/modules/` e l'automazione negli hook di `azd` descritti in [infra/README.md](infra/README.md).
- **Documentazione in italiano:** aggiorna il README della cartella che modifichi; il sito GitHub Pages viene generato dai README.
- **Media verificabili:** screenshot e MP4 in `docs/images` e `docs/videos` devono usare solo il corpus sintetico. Distingui risposte reali dei servizi da mock, schemi di architettura da flussi osservati e sessioni locali da accessi Easy Auth. Oscura identità, ID e segreti; conserva la data e i risultati osservati nel manifest.

## Aggiornare screenshot e video

Segui la [procedura di acquisizione](docs/demo-guide.md#rigenerare-screenshot-e-registrazioni) con [capture_demo_media.py](scripts/capture_demo_media.py) e dipendenze opzionali [media-requirements.txt](scripts/media-requirements.txt). Le query sono live e possono generare costi; non modificare utenti o appartenenze per rendere il risultato coerente con il copione. Verifica visivamente tutti gli screenshot e i video prima del commit, anche quando lo script ha terminato senza errori.

GitHub Pages pubblica gli stessi asset del repository; non committare build `site`/`.site-src`, video temporanei o profili browser. Mantieni gli MP4 sotto il limite GitHub di 100 MiB per file e scaricali prima della sessione per provarne la riproduzione offline. Il player del sito usa `preload="none"`.

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
