# arbitraggio-scanner

Worker Python sempre attivo per Railway, attualmente in **modalita test**.
Invia subito un heartbeat a Supabase e ripete ogni **15 secondi** usando `httpx`.
Non contiene fonti quote, scraping, calcolo arbitraggio o operazioni di scommessa.
La modalita test invia heartbeat reali: non e un dry run.

## Contratto del backend Supabase

Il worker usa la Data API REST e fa un upsert nella tabella
`public.worker_heartbeats`, con conflitto su `worker_id`. Mantiene una sola riga
per worker, evitando uno storico in crescita. Il repository non modifica
automaticamente il database. Se il backend esistente usa un altro contratto,
adattare endpoint e payload prima del deploy.

Per predisporre una nuova tabella, eseguire una volta nel SQL Editor Supabase:

```sql
create table public.worker_heartbeats (
  worker_id text primary key,
  mode text not null check (mode = 'test'),
  status text not null check (status = 'alive'),
  last_seen_at timestamptz not null
);
alter table public.worker_heartbeats enable row level security;
revoke all on table public.worker_heartbeats from anon, authenticated;
grant select, insert, update on table public.worker_heartbeats to service_role;
```

Abilitare la Data API e verificare che lo schema `public` sia esposto.
La chiave server autorizza l'upsert; non occorrono policy di accesso pubblico.
`status=alive` indica l'ultimo heartbeat ricevuto, non garantisce che il processo
sia ancora attivo: per monitorarlo verificare la freschezza di `last_seen_at`
(ad esempio segnalare un'assenza oltre 60 secondi).

## Variabili d'ambiente

Configurare i valori nelle **Variables** del servizio Railway, mai nel repository.

| Variabile | Obbligatoria | Valore |
| --- | --- | --- |
| `SUPABASE_URL` | Si | Origine HTTPS del progetto, senza `/rest/v1` |
| `SUPABASE_SECRET_KEY` | Si, oppure la variabile legacy sotto | Secret key server Supabase |
| `SUPABASE_SERVICE_ROLE_KEY` | Alternativa legacy | JWT service-role, usato solo se manca `SUPABASE_SECRET_KEY` |
| `WORKER_MODE` | No | `test` (predefinito e unica modalita supportata) |
| `WORKER_ID` | No | `arbitraggio-scanner`; assegnare ID distinti a worker indipendenti |

Le chiavi server hanno privilegi elevati: conservarle solo nelle variabili del
servizio. La secret key usa l'header `apikey`; il JWT legacy usa anche `Bearer`.
URL, chiavi, header e corpi delle risposte non vengono stampati nei log.
I file `.env*` sono ignorati da Git, ma il worker **non li carica automaticamente**.

## Avvio locale

Con Python 3.10 o successivo:

```sh
python -m venv .venv
# Attivare .venv nel proprio terminale
python -m pip install -r requirements.txt
# Impostare le variabili d'ambiente sopra nel terminale
python worker.py --once
python worker.py
```

`--once` effettua una singola scrittura reale: exit code 0 se riuscita, 1 se la
richiesta fallisce, 2 se la configurazione non e valida. Per test senza rete usare
`httpx.MockTransport` passando un client a `send_heartbeat`.

## Deploy Railway

1. Creare un servizio dal repository GitHub e scegliere il branch `main`.
2. Preparare la tabella Supabase e aggiungere le variabili d'ambiente.
3. Railway legge `railway.toml`, usa Railpack e installa `requirements.txt`.
   Il comando di avvio e `python -u worker.py`; la restart policy e `ALWAYS`.
4. Usare una replica, nessuna schedulazione cron e disabilitare Serverless/sleep
   nelle impostazioni del servizio. Non servono dominio pubblico, porta HTTP
   o healthcheck HTTP: e un processo in background.
5. Eseguire il deploy e controllare nei log `Heartbeat inviato (mode=test)`.
   Verificare nel database che `last_seen_at` avanzi circa ogni 15 secondi:

```sql
select worker_id, mode, status, last_seen_at,
       now() - last_seen_at as heartbeat_age
from public.worker_heartbeats;
```

La disponibilita continua e la policy Always dipendono dal piano Railway:
verificarne il supporto e le risorse disponibili. Un push prepara il codice;
il funzionamento end-to-end richiede tabella e variabili configurate.

## Errori e arresto

Timeout di rete limitati (connessione 5s, operazioni 10s). Errori HTTP o di rete
sono registrati senza dati sensibili e il ciclo ritenta al prossimo intervallo.
Controllare credenziali per 401/403, tabella/Data API per 404 e schema per 400.
Non vengono seguiti redirect. Gli errori di configurazione terminano il processo.
SIGTERM e Ctrl+C interrompono l'attesa; una richiesta in corso termina entro i
timeout di rete. Un arresto non aggiorna lo stato: fa fede `last_seen_at`.

## Riferimenti

- [Railway: configurazione come codice](https://docs.railway.com/config-as-code/reference)
- [Railway: restart policy](https://docs.railway.com/deployments/restart-policy)
- [Supabase: chiavi API](https://supabase.com/docs/guides/getting-started/api-keys)
- [PostgREST: upsert](https://docs.postgrest.org/en/stable/references/api/tables_views.html#upsert)
