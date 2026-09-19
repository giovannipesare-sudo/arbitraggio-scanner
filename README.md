# arbitraggio-scanner

Worker Python sempre attivo per Railway, attualmente in **modalita test**.
Invia subito un heartbeat a Supabase e ripete ogni **15 secondi** usando `httpx`.
Nell'avvio normale controlla solo lo stato account OddsPapi. Un comando esplicito
consente un singolo snapshot di collaudo. Non fa scraping, calcolo arbitraggio
o operazioni di scommessa.
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
| `ODDSPAPI_API_KEY` | No | Chiave OddsPapi; se assente la fonte viene salvata come `disabled` |
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

`--once` invia un heartbeat e aggiorna una volta lo stato della fonte, con scritture
reali: exit code 0 se entrambi i salvataggi riescono, 1 se uno fallisce, 2 per
configurazione non valida. Un account offline salvato correttamente non causa
exit code 1. I test automatici non usano rete o credenziali reali:

```sh
python -m unittest discover -s tests -v
```

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


## OddsPapi: controllo account nell'avvio normale

Nell'avvio normale, con `ODDSPAPI_API_KEY` impostata, l'unica richiesta al provider e
`GET https://api.oddspapi.io/v4/account?apiKey=...`, all'avvio e ogni 300 secondi.
Non vengono seguiti redirect e non sono previsti retry immediati. Non vengono
chiamati `/odds`, `/fixtures`, `/tournaments`, `/bookmakers`, `/markets` o altri
endpoint del provider. Non si apre alcuna connessione WebSocket.

Il controllo usa un thread separato dall'heartbeat Supabase ogni 15 secondi.
Senza chiave, nessuna richiesta parte verso OddsPapi; lo stato `disabled` viene
comunque aggiornato in Supabase ogni 5 minuti. `WORKER_MODE=test` resta obbligatorio.

La subscription viene scelta tra quelle con `is_active=true`, usando
`current_subscription_id` quando presente. Assenza, ambiguita o campi non validi
producono `offline`. Non si sommano quote o bookmaker di subscription diverse.

- `online`: account valido con subscription attiva e oltre il 10% della quota residua.
- `degraded`: quota residua minore o uguale al 10%, inclusa quota esaurita o limite zero.
- `offline`: errore HTTP, rete, JSON/schema o nessuna subscription attiva.
- `disabled`: chiave assente.

`metadata` contiene solo `request_limit`, `request_count`, `remaining_requests`
(calcolato come `max(0, request_limit - request_count)`), `websocket_access`,
`sport_ids`, `bookmaker_count` e `bookmaker_slugs` (ordinati). Si accettano
contatori interi JSON non negativi (non booleani); `websocket_access` accetta
anche booleani, normalizzati a 0/1. `sport_ids` richiede una lista di interi
non negativi. I bookmaker richiedono un oggetto con chiavi stringa non vuote,
senza restrizioni regex sugli slug. Gli errori di campo sono distinti:
`invalid_request_limit`, `invalid_request_count`, `invalid_websocket_access`,
`invalid_sport_ids`, `invalid_bookmakers`. Limiti null o altri formati non documentati
vengono segnalati offline invece di essere interpretati come quota illimitata.
La risposta grezza e il campo `api_key` non vengono mai persistiti. I log HTTP
sono disattivati sotto WARNING e gli errori salvati sono codici controllati,
mai URL, eccezioni complete o messaggi restituiti dal provider.

## Tabella scanner_status

Il worker effettua upsert in `public.scanner_status` con conflitto su
`component_key`, sempre `oddspapi`, e `component_type=source`. Predisporre questa
struttura prima del deploy. Se la tabella esiste gia, verificarne colonne, chiave
univoca e vincoli di stato: non eseguire CREATE su una tabella esistente.
Il repository non modifica il database automaticamente.

```sql
create table public.scanner_status (
  component_key text primary key,
  component_type text not null,
  status text not null check (status in ('disabled', 'online', 'degraded', 'offline')),
  metadata jsonb not null default '{}'::jsonb,
  last_heartbeat_at timestamptz not null,
  last_success_at timestamptz,
  last_error text
);
alter table public.scanner_status enable row level security;
revoke all on table public.scanner_status from anon, authenticated;
grant select, insert, update on table public.scanner_status to service_role;
```

`last_heartbeat_at` registra ogni controllo completato (anche disabled/offline).
`last_success_at` viene aggiornato solo per account valido con subscription
attiva, anche degraded; su errori o disabled viene omesso dall'upsert per
preservare l'ultimo successo nel database, anche dopo un riavvio. Su una nuova
riga senza successi resta NULL. `last_error` e NULL su successo o disabled,
altrimenti contiene un codice di errore senza segreti. Su offline/disabled
`metadata` diventa `{}`, per non presentare come correnti dati precedenti.

Un errore di scrittura Supabase viene registrato e ritentato al ciclo seguente;
se il database e irraggiungibile, i timestamp salvati restano quelli precedenti.
Per monitorare la fonte valutare anche la freschezza di `last_heartbeat_at`
(intervallo nominale 5 minuti, distinto dall'heartbeat del worker).

```sql
select component_key, status, metadata, last_heartbeat_at, last_success_at, last_error
from public.scanner_status where component_key = 'oddspapi';
```

Riferimenti: [risposta account](https://oddspapi.io/en/docs/get-account) e
[regole di quota](https://oddspapi.io/us/docs/requests-and-quota).

## Collaudo quote one-shot (esplicito)

```sh
python worker.py --oddspapi-test
```

Richiede le variabili Supabase, `ODDSPAPI_API_KEY` e `WORKER_MODE=test`.
Il comando termina dopo un solo snapshot. Non impostarlo come start command
Railway: la policy ALWAYS lo rilancerebbe. Avviarlo manualmente in una sessione
separata mentre il normale worker continua heartbeat e controllo account.
Non combinarlo con `--once`. Nessun polling quote, notifica o scommessa.

Prima controlla `/account`, subscription, calcio (sportId 10), bookmaker consentiti
e almeno **3 richieste residue**; verifica poi in lettura mapping e tabelle
Supabase. Quota insufficiente o mapping mancanti bloccano prima delle chiamate
billable. Altri client possono consumare quota contemporaneamente.

Il percorso completo usa **3 richieste billable**, con limite assoluto **4**:

1. `/v4/tournaments?sportId=10&language=en`: seleziona Serie A nella categoria Italy;
   assenza o ambiguita interrompono il test.
2. `/v4/markets?language=en`: identifica i mercati calcio fulltime, non player-prop,
   1X2 e Over/Under 2.5 e 3.5 con relativi outcome.
3. `/v4/odds-by-tournaments`: un solo snapshot del torneo, con `verbosity=3`,
   quote decimali e filtro esplicito bookmaker.

Ogni tentativo conta prima dell'invio, anche in caso di errore. Nessun retry,
redirect, paginazione o secondo snapshot. Non si chiamano `/odds`, `/fixtures`,
`/bookmakers` o `/participants`. Gli slug ammessi sono solo:
`888sport.it`, `admiralbet.it`, `bet365.it`, `betfair-ex`, `betflag.it`,
`betsson.it`, `eurobet.it`, `lottomatica.it`, `sisal.it`, `snai.it`,
intersecati con la subscription attiva.

L'endpoint per torneo non documenta un filtro pre-match lato server: eventuali
live nella risposta sono scartati prima del salvataggio. Si accettano solo
status 0/1, startTime futuro e assenza di trueStartTime/trueEndTime. Si escludono
bookmaker, mercati e outcome inattivi/sospesi, player-prop e prezzi non finiti
o <=1. Per gli exchange si salva solo il prezzo decimale `back` fornito;
non si deducono lay, liquidita o commissioni da exchangeMeta.

### Persistenza nelle tabelle esistenti

Schema verificato in lettura sul progetto ARBITRAGGIO, senza modifiche al database.
Servono `quote_sources.code=ODDSPAPI` abilitata e mapping attivi tramite
`bookmakers.oddspapi_slug`.

- `events`: ricerca per `external_keys.oddspapi=fixtureId`, aggiornamento per ID
  oppure inserimento. Mantiene altre external_keys. Se i nomi non sono forniti,
  usa etichette esplicite con participantId, senza altre chiamate provider.
- `markets`: upsert su `(event_id, normalized_key)`, chiavi `1x2`, `ou_2.5`,
  `ou_3.5`, tipi `1x2`/`over_under`, periodo `full_time`, esiti `1`, `X`, `2`,
  `over`, `under`.
- `quotes_current`: upsert su
  `(market_id, bookmaker_id, source_id, outcome_code, side)`.
- `quote_history`: inserimento delle osservazioni del collaudo.

`bookmaker_changed_at` usa bookmakerChangedAt, con changedAt come fallback.
`received_at` e l'istante UTC di ricezione. Non esiste una colonna changed_at:
`raw_ref` conserva un piccolo JSON costruito con identificativi e changedAt,
senza risposta grezza, URL o segreti. Se entrambi i timestamp mancano,
bookmaker_changed_at resta NULL.

Eseguire un solo collaudo alla volta: events non ha un vincolo univoco sul suo
identificativo JSON. Le scritture REST non sono una transazione unica: un errore
puo lasciare salvataggi parziali. Una nuova esecuzione manuale aggiorna eventi,
mercati e quote correnti ma aggiunge osservazioni allo storico.

Exit code: 0 per completamento (anche senza quote ammissibili), 1 per fallimento,
2 per configurazione/argomenti non validi. Log limitati a conteggi e codici sicuri.
I test usano `httpx.MockTransport`, senza consumo di quota reale.

Riferimenti: [tornei](https://oddspapi.io/en/docs/get-tournaments),
[mercati](https://oddspapi.io/en/docs/get-markets),
[snapshot per torneo](https://oddspapi.io/en/docs/get-odds-by-tournaments).


### Diagnostica del collaudo

Tra due richieste billable viene eseguita una pausa di almeno 1,5 secondi dopo
la conclusione della precedente. Solo `/v4/odds-by-tournaments` usa timeout HTTPX
30 secondi (connessione, lettura, scrittura e pool); gli altri timeout e il
controllo `/account` restano invariati. Non ci sono retry automatici.

Gli errori HTTP producono solo un codice sicuro e il contatore, per esempio:
`odds_by_tournaments_http_429 billable_attempted=3`. I prefissi sono
`tournaments`, `markets`, `odds_by_tournaments`; per timeout o rete il suffisso
 e `_network_error`. Il conteggio include la richiesta fallita, esclude account
 e Supabase ed e zero se il preflight blocca il test. Il limite resta 4.
Nessun URL, query string, body, header o credenziale viene incluso nei log.
Il codice distingue endpoint e categoria HTTP/rete senza esporre i dettagli
potenzialmente sensibili restituiti dal provider.

La suite locale simula HTTP 400/403/429/500, timeout e problemi di rete con
`httpx.MockTransport`; la pausa e simulata e verificata, senza richieste reali.
