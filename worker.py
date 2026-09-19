"""Worker di test: heartbeat Supabase e controllo account opzionale, senza quote."""

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
ACCOUNT_INTERVAL_SECONDS = 300
ACCOUNT_URL = "https://api.oddspapi.io/v4/account"
# Suppress HTTP request URLs, which contain the OddsPapi credential.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


@dataclass(frozen=True)
class Settings:
    url: str
    key: str = field(repr=False)
    worker_id: str = "arbitraggio-scanner"

    oddspapi_key: str = field(default="", repr=False)

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
        return cls(url, key, worker_id, os.environ.get("ODDSPAPI_API_KEY", "").strip())


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


def account_metadata(data: dict) -> dict:
    """Validate and allowlist fields; never persist a raw account response."""
    if not isinstance(data, dict) or not isinstance(data.get("subscriptions"), list):
        raise ValueError("invalid_account_response")
    active = [s for s in data["subscriptions"] if isinstance(s, dict) and s.get("is_active") is True]
    if not active:
        raise ValueError("no_active_subscription")
    current = data.get("current_subscription_id")
    selected = [s for s in active if s.get("subscription_id") == current] if current else active
    if len(selected) != 1:
        raise ValueError("ambiguous_active_subscription")
    sub = selected[0]
    limit, count = sub.get("request_limit"), sub.get("request_count")
    websocket = sub.get("websocket_access")
    sports, bookmakers = sub.get("sport_ids"), sub.get("bookmakers")
    if type(limit) is not int or limit < 0:
        raise ValueError("invalid_request_limit")
    if type(count) is not int or count < 0:
        raise ValueError("invalid_request_count")
    if isinstance(websocket, bool):
        websocket = int(websocket)
    elif type(websocket) is not int or websocket < 0:
        raise ValueError("invalid_websocket_access")
    if not isinstance(sports, list) or any(type(n) is not int or n < 0 for n in sports):
        raise ValueError("invalid_sport_ids")
    if not isinstance(bookmakers, dict) or any(
        not isinstance(slug, str) or not slug for slug in bookmakers
    ):
        raise ValueError("invalid_bookmakers")
    # A returned credential must not be copied even if echoed in a bookmaker key.
    returned_key = data.get("api_key")
    if isinstance(returned_key, str) and returned_key and any(
        returned_key in slug for slug in bookmakers
    ):
        raise ValueError("invalid_bookmakers")
    slugs = sorted(bookmakers)
    return {"request_limit": limit, "request_count": count,
            "remaining_requests": max(0, limit - count), "websocket_access": websocket,
            "sport_ids": sports, "bookmaker_count": len(slugs), "bookmaker_slugs": slugs}


def check_account(client: httpx.Client, settings: Settings) -> dict:
    """Only OddsPapi /account is allowed. Errors are fixed codes, never API text."""
    payload = {"component_key": "oddspapi", "component_type": "source",
               "status": "disabled", "metadata": {}, "last_error": None}
    if settings.oddspapi_key:
        try:
            response = client.get(ACCOUNT_URL, params={"apiKey": settings.oddspapi_key},
                                  follow_redirects=False)
            response.raise_for_status()
            metadata = account_metadata(response.json())
            # Defense against a response echoing the credential in an allowed string field.
            if any(settings.oddspapi_key in slug for slug in metadata["bookmaker_slugs"]):
                raise ValueError("invalid_bookmakers")
            payload["metadata"] = metadata
            payload["status"] = ("degraded" if metadata["remaining_requests"] <=
                                 metadata["request_limit"] * 0.1 else "online")
            payload["last_success_at"] = datetime.now(timezone.utc).isoformat()
        except httpx.HTTPStatusError as exc:
            payload.update(status="offline", last_error=f"account_http_{exc.response.status_code}")
        except httpx.RequestError:
            payload.update(status="offline", last_error="account_network_error")
        except ValueError as exc:
            safe_codes = {"no_active_subscription", "ambiguous_active_subscription",
                          "invalid_request_limit", "invalid_request_count",
                          "invalid_websocket_access", "invalid_sport_ids",
                          "invalid_bookmakers", "invalid_account_response"}
            code = str(exc)
            payload.update(status="offline", last_error=code if code in safe_codes else "invalid_account_response")
    payload["last_heartbeat_at"] = datetime.now(timezone.utc).isoformat()
    return payload


def update_source_status(client: httpx.Client, settings: Settings) -> bool:
    payload = check_account(client, settings)
    headers = {"apikey": settings.key, "Content-Profile": "public",
               "Prefer": "resolution=merge-duplicates,return=minimal"}
    if not settings.key.startswith("sb_secret_"):
        headers["Authorization"] = f"Bearer {settings.key}"
    try:
        response = client.post(f"{settings.url}/rest/v1/scanner_status",
                               params={"on_conflict": "component_key"},
                               headers=headers, json=payload, follow_redirects=False)
        response.raise_for_status()
    except httpx.HTTPError:
        LOG.warning("Salvataggio scanner_status fallito; nuovo tentativo al prossimo ciclo.")
        return False
    LOG.info("Stato OddsPapi salvato: %s.", payload["status"])
    return True


def run_source(client: httpx.Client, settings: Settings, stop: threading.Event) -> None:
    while not stop.is_set():
        started = time.monotonic()
        update_source_status(client, settings)
        remaining = ACCOUNT_INTERVAL_SECONDS - (time.monotonic() - started)
        stop.wait(remaining if remaining > 0 else ACCOUNT_INTERVAL_SECONDS)


def run(client: httpx.Client, settings: Settings, stop: threading.Event) -> None:
    while not stop.is_set():
        started = time.monotonic()
        send_heartbeat(client, settings)
        # Cadenza tra gli inizi delle richieste; nessun recupero a raffica dei cicli persi.
        remaining = HEARTBEAT_INTERVAL_SECONDS - (time.monotonic() - started)
        stop.wait(remaining if remaining > 0 else HEARTBEAT_INTERVAL_SECONDS)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_mutually_exclusive_group()
    commands.add_argument("--oddspapi-test", action="store_true", help="Collaudo quote esplicito: un solo snapshot, massimo 4 richieste billable.")
    commands.add_argument("--once", action="store_true", help="Invia heartbeat e stato fonte una volta, poi termina.")
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
        if args.oddspapi_test:
            from oddspapi_test import run_test
            return run_test(client, settings, check_account, LOG)
        if args.once:
            heartbeat_ok = send_heartbeat(client, settings)
            source_ok = update_source_status(client, settings)
            return 0 if heartbeat_ok and source_ok else 1
        source_thread = threading.Thread(target=run_source, args=(client, settings, stop),
                                         name="oddspapi-status")
        source_thread.start()
        try:
            run(client, settings, stop)
        finally:
            stop.set()
            source_thread.join()
    LOG.info("Worker arrestato.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
