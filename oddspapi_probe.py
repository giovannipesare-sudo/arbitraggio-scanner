"""Explicit snapshot connectivity probe: no persistence, parsing or retries."""
import httpx


def run_probe(client, settings, account_check, logger):
    if not settings.oddspapi_key:
        logger.error('oddspapi_key_missing billable_attempted=0')
        return 1
    account = account_check(client, settings)
    if account['status'] not in ('online', 'degraded'):
        logger.error('account_not_ready billable_attempted=0')
        return 1
    metadata = account['metadata']
    if metadata['remaining_requests'] < 1:
        logger.error('insufficient_quota billable_attempted=0')
        return 1
    if 10 not in metadata['sport_ids']:
        logger.error('soccer_not_enabled billable_attempted=0')
        return 1
    if 'bet365.it' not in metadata['bookmaker_slugs']:
        logger.error('bet365_not_enabled billable_attempted=0')
        return 1
    try:
        response = client.get(
            'https://api.oddspapi.io/v4/odds-by-tournaments',
            params={'tournamentIds': '23', 'bookmaker': 'bet365.it',
                    'apiKey': settings.oddspapi_key},
            timeout=30.0, follow_redirects=False,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        logger.error('odds_by_tournaments_http_%s billable_attempted=1',
                     exc.response.status_code)
        return 1
    except httpx.RequestError:
        logger.error('odds_by_tournaments_network_error billable_attempted=1')
        return 1
    logger.info('snapshot_probe_ok billable_attempted=1')
    return 0
