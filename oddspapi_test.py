"""Explicit, bounded OddsPapi snapshot test. Never called by the polling worker."""
import json
import math
from datetime import datetime, timezone

import httpx

BOOKMAKERS = ('888sport.it', 'admiralbet.it', 'bet365.it', 'betfair-ex',
              'betflag.it', 'betsson.it', 'eurobet.it', 'lottomatica.it', 'sisal.it', 'snai.it')
MAX_BILLABLE = 4
PLANNED_BILLABLE = 3
SPORT_ID = 10  # Soccer, documented OddsPapi v4 identifier.


class TestFailure(Exception):
    """Only fixed, non-sensitive error codes are passed to this exception."""


def stamp(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise TestFailure('invalid_timestamp')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError
        return parsed.astimezone(timezone.utc).isoformat()
    except ValueError:
        raise TestFailure('invalid_timestamp') from None


def identifier(value):
    if type(value) is int and value >= 0:
        return str(value)
    if isinstance(value, str) and value and len(value) <= 200:
        return value
    raise TestFailure('invalid_identifier')


class Provider:
    def __init__(self, client, key, remaining):
        self.client, self.key = client, key
        self.remaining, self.used = remaining, 0
        self.called = set()

    def get(self, endpoint, params):
        if endpoint not in ('tournaments', 'markets', 'odds-by-tournaments'):
            raise TestFailure('endpoint_not_allowed')
        if endpoint in self.called:
            raise TestFailure('duplicate_request_blocked')
        if self.used >= MAX_BILLABLE or self.used >= self.remaining:
            raise TestFailure('request_budget_exhausted')
        # Count attempts before sending, including HTTP failures and timeouts.
        self.used += 1
        self.called.add(endpoint)
        response = self.client.get('https://api.oddspapi.io/v4/' + endpoint,
                                   params={**params, 'apiKey': self.key}, follow_redirects=False)
        response.raise_for_status()
        return response.json()


def serie_a(rows):
    if not isinstance(rows, list):
        raise TestFailure('invalid_tournaments')
    matches = [r for r in rows if isinstance(r, dict)
               and str(r.get('categorySlug', '')).lower() == 'italy'
               and (str(r.get('tournamentSlug', '')).lower() == 'serie-a'
                    or str(r.get('tournamentName', '')).lower() == 'serie a')]
    if len(matches) != 1:
        raise TestFailure('serie_a_not_unique')
    return identifier(matches[0].get('tournamentId'))


def market_catalog(rows):
    if not isinstance(rows, list):
        raise TestFailure('invalid_market_catalog')
    result, found = {}, set()
    for row in rows:
        if not isinstance(row, dict) or row.get('sportId') != SPORT_ID:
            continue
        if row.get('period') != 'fulltime' or row.get('playerProp') is not False:
            continue
        kind, line = row.get('marketType'), row.get('handicap')
        if kind == '1x2' and line == 0:
            key, expected = '1x2', {'1', 'X', '2'}
            line = None
        elif kind == 'totals' and line in (2.5, 3.5):
            key, expected = f'ou_{line}', {'over', 'under'}
        else:
            continue
        outcomes = row.get('outcomes')
        if not isinstance(outcomes, list):
            raise TestFailure('invalid_market_outcomes')
        mapping = {}
        for outcome in outcomes:
            label = outcome.get('outcomeName')
            label = str(label).upper() if kind == '1x2' else str(label).lower()
            if label not in expected:
                raise TestFailure('invalid_market_outcomes')
            mapping[identifier(outcome.get('outcomeId'))] = label
        if set(mapping.values()) != expected or len(mapping) != len(expected) or key in found:
            raise TestFailure('ambiguous_market_catalog')
        found.add(key)
        result[identifier(row.get('marketId'))] = {
            'market_type': '1x2' if kind == '1x2' else 'over_under',
            'line': line, 'period': 'full_time', 'normalized_key': key, 'outcomes': mapping}
    if found != {'1x2', 'ou_2.5', 'ou_3.5'}:
        raise TestFailure('required_markets_missing')
    return result


def normalize(snapshot, tournament, catalog, allowed, captured):
    if isinstance(snapshot, dict) and 'fixtureId' in snapshot:
        snapshot = [snapshot]
    if not isinstance(snapshot, list):
        raise TestFailure('invalid_snapshot_shape')
    normalized = []
    for fixture in snapshot:
        if not isinstance(fixture, dict):
            raise TestFailure('invalid_fixture')
        if fixture.get('sportId') != SPORT_ID or str(fixture.get('tournamentId')) != tournament:
            continue
        # Fail closed: only explicitly pre-game/scheduled and future, never started/ended.
        if type(fixture.get('statusId')) is not int or fixture['statusId'] not in (0, 1):
            continue
        if fixture.get('trueStartTime') or fixture.get('trueEndTime'):
            continue
        start = stamp(fixture.get('startTime'))
        if start is None or datetime.fromisoformat(start) <= captured:
            continue
        fixture_id = identifier(fixture.get('fixtureId'))
        home = fixture.get('participant1Name') or 'OddsPapi participant ' + identifier(fixture.get('participant1Id'))
        away = fixture.get('participant2Name') or 'OddsPapi participant ' + identifier(fixture.get('participant2Id'))
        if not isinstance(home, str) or not isinstance(away, str):
            raise TestFailure('invalid_participants')
        event = {'sport': 'football', 'competition': 'Serie A', 'home_participant': home,
                 'away_participant': away, 'event_name': home + ' - ' + away,
                 'start_time': start, 'status': 'scheduled', 'external_keys': {'oddspapi': fixture_id}}
        quotes = []
        for slug, bookmaker in fixture.get('bookmakerOdds', {}).items():
            if slug not in allowed or bookmaker.get('bookmakerIsActive') is not True or bookmaker.get('suspended') is not False:
                continue
            for market_id, market in bookmaker.get('markets', {}).items():
                spec = catalog.get(str(market_id))
                if not spec or market.get('marketActive') is not True:
                    continue
                for outcome_id, outcome in market.get('outcomes', {}).items():
                    code = spec['outcomes'].get(str(outcome_id))
                    price_data = outcome.get('players', {}).get('0')
                    if not code or not isinstance(price_data, dict) or price_data.get('active') is not True:
                        continue
                    price = price_data.get('price')
                    if type(price) not in (int, float) or not math.isfinite(price) or price <= 1:
                        continue
                    bookmaker_changed = stamp(price_data.get('bookmakerChangedAt'))
                    changed = stamp(price_data.get('changedAt'))
                    # No raw provider payload/URLs. Preserve both timestamps in existing columns.
                    reference = json.dumps({'provider': 'oddspapi', 'fixture_id': fixture_id,
                                            'market_id': str(market_id), 'outcome_id': str(outcome_id),
                                            'changedAt': changed}, separators=(',', ':'))
                    quotes.append((slug, {k: v for k, v in spec.items() if k != 'outcomes'},
                                   {'outcome_code': code, 'side': 'back', 'odds': price,
                                    'available_amount_eur': None, 'is_active': True, 'is_suspended': False,
                                    'bookmaker_changed_at': bookmaker_changed or changed,
                                    'received_at': captured.isoformat(), 'raw_ref': reference}))
        if quotes:
            normalized.append((event, quotes))
    return normalized


class Store:
    def __init__(self, client, settings):
        self.client, self.settings = client, settings
        self.headers = {'apikey': settings.key, 'Accept-Profile': 'public', 'Content-Profile': 'public',
                        'Prefer': 'resolution=merge-duplicates,return=representation'}
        if not settings.key.startswith('sb_secret_'):
            self.headers['Authorization'] = 'Bearer ' + settings.key

    def request(self, method, table, params=None, payload=None):
        response = self.client.request(method, self.settings.url + '/rest/v1/' + table,
                                       params=params, json=payload, headers=self.headers, follow_redirects=False)
        response.raise_for_status()
        return response.json()

    def preflight(self, allowed):
        sources = self.request('GET', 'quote_sources', {'code': 'eq.ODDSPAPI', 'enabled': 'eq.true', 'select': 'id'})
        rows = self.request('GET', 'bookmakers', {'active': 'eq.true', 'select': 'id,oddspapi_slug'})
        if len(sources) != 1:
            raise TestFailure('source_mapping_missing')
        self.source_id = sources[0]['id']
        self.bookmakers = {}
        for row in rows:
            slug = row['oddspapi_slug']
            if slug in allowed:
                if slug in self.bookmakers:
                    raise TestFailure('ambiguous_bookmaker_mapping')
                self.bookmakers[slug] = row['id']
        if set(self.bookmakers) != set(allowed):
            raise TestFailure('bookmaker_mapping_missing')
        # Validate access/column contract before consuming any billable request.
        for table, columns in [('events', 'id,external_keys,event_name,start_time'),
                               ('markets', 'id,event_id,normalized_key,market_type,line,period'),
                               ('quotes_current', 'id,market_id,bookmaker_changed_at,received_at,raw_ref'),
                               ('quote_history', 'id,market_id,bookmaker_changed_at,received_at,raw_ref')]:
            self.request('GET', table, {'select': columns, 'limit': '0'})

    def save(self, rows):
        count = 0
        for event, quotes in rows:
            existing = self.request('GET', 'events', {'external_keys': 'cs.' + json.dumps(event['external_keys']), 'select': 'id,external_keys'})
            if len(existing) > 1:
                raise TestFailure('ambiguous_existing_event')
            if existing:
                event['external_keys'] = {**existing[0]['external_keys'], **event['external_keys']}
                saved = self.request('PATCH', 'events', {'id': 'eq.' + str(existing[0]['id'])}, event)
            else:
                saved = self.request('POST', 'events', payload=event)
            event_id = saved[0]['id']
            market_ids = {}
            for slug, market, quote in quotes:
                key = market['normalized_key']
                if key not in market_ids:
                    saved = self.request('POST', 'markets', {'on_conflict': 'event_id,normalized_key'},
                                         {**market, 'event_id': event_id})
                    market_ids[key] = saved[0]['id']
                row = {**quote, 'market_id': market_ids[key], 'bookmaker_id': self.bookmakers[slug], 'source_id': self.source_id}
                self.request('POST', 'quotes_current', {'on_conflict': 'market_id,bookmaker_id,source_id,outcome_code,side'}, row)
                self.request('POST', 'quote_history', payload=row)
                count += 1
        return count


def run_test(client, settings, account_check, logger):
    """One explicit invocation. No retry, pagination, background loop or key logging."""
    try:
        if not settings.oddspapi_key:
            raise TestFailure('oddspapi_key_missing')
        account = account_check(client, settings)
        if account['status'] not in ('online', 'degraded'):
            raise TestFailure('account_not_ready')
        metadata = account['metadata']
        if metadata['remaining_requests'] < PLANNED_BILLABLE:
            raise TestFailure('insufficient_quota')
        if SPORT_ID not in metadata['sport_ids']:
            raise TestFailure('soccer_not_enabled')
        allowed = tuple(slug for slug in BOOKMAKERS if slug in metadata['bookmaker_slugs'])
        if not allowed:
            raise TestFailure('no_allowed_bookmakers')
        store = Store(client, settings)
        store.preflight(allowed)
        provider = Provider(client, settings.oddspapi_key, metadata['remaining_requests'])
        tournament = serie_a(provider.get('tournaments', {'sportId': SPORT_ID, 'language': 'en'}))
        catalog = market_catalog(provider.get('markets', {'language': 'en'}))
        snapshot = provider.get('odds-by-tournaments', {'tournamentIds': tournament,
                                'bookmakers': ','.join(allowed), 'language': 'en',
                                'verbosity': 3, 'oddsFormat': 'decimal'})
        captured = datetime.now(timezone.utc)
        rows = normalize(snapshot, tournament, catalog, allowed, captured)
        encoded = json.dumps(rows, ensure_ascii=False)
        if any(key and key in encoded for key in (settings.key, settings.oddspapi_key)):
            raise TestFailure('unsafe_normalized_data')
        count = store.save(rows)
        logger.info('Collaudo completato: billable=%s eventi=%s quote=%s.', provider.used, len(rows), count)
        return 0
    except TestFailure as exc:
        logger.error('Collaudo interrotto: %s.', exc)
    except httpx.HTTPError:
        logger.error('Collaudo interrotto: errore HTTP/rete; nessun retry automatico.')
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, OverflowError):
        logger.error('Collaudo interrotto: risposta o schema non valido.')
    return 1
