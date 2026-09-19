"""Worker di test: pubblica solo heartbeat, senza fonti quote."""

import argparse
import logging
import os
import signal
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx

LOG = logging.getLogger("worker")
HEARTBEAT_INTERVAL_SECONDS = 15


@dataclass(frozen=True)
class Settings:
    url: str
    key: str = field(repr=False)
    worker_id: str = "arbitraggio-scanner"

    @classmethod
    def from_env(cls):
        url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
        key = os.environ.get("SUPABASE_SECRET_KEY", "").strip()
        if not key:
            key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        if not url or not key:
            raise ValueError("Impostare SUPABASE_URL e SUPABASE_SECRET_KEY (o SUPABASE_SERVICE_ROLE_KEY).")
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username
                or parsed.password or parsed.path or parsed.query or parsed.fragment):
            raise ValueError("SUPABASE_URL deve essere l'origine HTTPS del progetto, senza percorso o credenziali.")
        if os.environ.get("WORKER_MODE", "test").strip().lower() != "test":
            raise ValueError("Solo WORKER_MODE=test e' implementato.")
        worker_id = os.environ.get("WORKER_ID", "arbitraggio-scanner").strip()
        if not worker_id or len(worker_id) > 128:
            raise ValueError("WORKER_ID deve contenere da 1 a 128 caratteri.")
        return cls(url, key, worker_id)


def send_heartbeat(client: httpx.Client, settings: Settings) -> bool:
    headers = {"apikey": settings.key,
               "Prefer": "resolution=merge-duplicates,return=minimal"}
    # Le nuove secret key non sono JWT e non vanno inviate come Bearer.
    if not settings.key.startswith("sb_secret_"):
        headers["Authorization"] = f"Bearer {settings.key}"
    payload = {
        "worker_id": settings.worker_id,
        "mode": "test",
        "status": "alive",
        "last_seen_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        response = client.post(
            f"{settings.url}/rest/v1/worker_heartbeats",
            params={"on_conflict": "worker_id"}, headers=headers, json=payload,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        # Non registrare URL, header, corpo della risposta o credenziali.
        LOG.warning("Heartbeat fallito: HTTP %s; nuovo tentativo al prossimo ciclo.",
                    exc.response.status_code)
        return False
    except httpx.RequestError:
        LOG.warning("Heartbeat fallito: errore di rete/timeout; nuovo tentativo al prossimo ciclo.")
        return False
    LOG.info("Heartbeat inviato (mode=test).")
    return True


def run(client: httpx.Client, settings: Settings, stop: threading.Event) -> None:
    while not stop.is_set():
        started = time.monotonic()
        send_heartbeat(client, settings)
        # Cadenza tra gli inizi delle richieste; nessun recupero a raffica dei cicli persi.
        remaining = HEARTBEAT_INTERVAL_SECONDS - (time.monotonic() - started)
        stop.wait(remaining if remaining > 0 else HEARTBEAT_INTERVAL_SECONDS)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Invia un heartbeat reale e termina.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    try:
        settings = Settings.from_env()
    except ValueError as exc:
        LOG.error("Configurazione non valida: %s", exc)
        return 2
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    LOG.info("Worker avviato: mode=test, intervallo=%ss.", HEARTBEAT_INTERVAL_SECONDS)
    with httpx.Client(timeout=httpx.Timeout(10.0, connect=5.0), follow_redirects=False) as client:
        if args.once:
            return 0 if send_heartbeat(client, settings) else 1
        run(client, settings, stop)
    LOG.info("Worker arrestato.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
