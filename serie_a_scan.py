"""Single Serie A scan. No scheduling, notifications or betting actions."""
import json
from datetime import datetime, timezone
from decimal import Decimal

import httpx

from oddspapi_test import Provider, Store, TestFailure, market_catalog, normalize, stamp

QUOTE_FIELDS = ('market_id', 'bookmaker_id', 'source_id', 'outcome_code', 'side',
                'odds', 'available_amount_eur', 'is_active', 'is_suspended',
                'bookmaker_changed_at', 'received_at', 'raw_ref')
KEY_FIELDS = ('market_id', 'bookmaker_id', 'source_id', 'outcome_code', 'side')
OP_FIELDS = ('market_id', 'opportunity_type', 'arb_index', 'roi_percent',
             'total_committed_eur', 'expected_return_eur', 'expected_profit_eur',
             'max_quote_age_ms', 'confidence_label', 'fingerprint')
LEG_FIELDS = ('bookmaker_id', 'source_id', 'outcome_code', 'side', 'odds',
              'suggested_stake_eur', 'liability_eur', 'quote_received_at', 'bookmaker_changed_at')


class ScanProvider(Provider):
    def get(self, endpoint, params):
        if endpoint not in ('markets', 'odds-by-tournaments'):
            raise TestFailure('endpoint_not_allowed')
        if self.used >= 2:
            raise TestFailure('request_budget_exhausted')
        return super().get(endpoint, params)


def quote_key(row):
    return tuple(row[k] for k in KEY_FIELDS)


def changed_at(row):
    try:
        ref = json.loads(row.get('raw_ref') or '{}')
        return stamp(ref.get('changedAt')) if isinstance(ref, dict) else None
    except (ValueError, TypeError, TestFailure):
        return None


def quote_changed(old, new):
    if old is None:
        return True
    return (Decimal(str(old['odds'])) != Decimal(str(new['odds']))
            or old['is_active'] != new['is_active']
            or old['is_suspended'] != new['is_suspended']
            or stamp(old.get('bookmaker_changed_at')) != stamp(new.get('bookmaker_changed_at'))
            or changed_at(old) != changed_at(new))


class ScanStore(Store):
    def request(self, method, table, params=None, payload=None):
        response = self.client.request(method, self.settings.url + '/rest/v1/' + table,
                                       params=params, json=payload, headers=self.headers, follow_redirects=False)
        response.raise_for_status()
        return response.json() if response.content else []

    def all_rows(self, table, params):
        # Explicit pagination prevents silently missing old quotes/opportunities.
        rows, offset = [], 0
        while True:
            page = self.request('GET', table, {**params, 'order': 'id', 'limit': 500, 'offset': offset})
            if not isinstance(page, list):
                raise TestFailure('invalid_storage_response')
            rows.extend(page)
            if len(page) < 500:
                return rows
            offset += len(page)

    def prepare(self):
        sources = self.request('GET', 'quote_sources', {'code': 'eq.ODDSPAPI', 'enabled': 'eq.true', 'select': 'id'})
        if len(sources) != 1:
            raise TestFailure('source_mapping_missing')
        self.source_id = sources[0]['id']
        books = self.all_rows('bookmakers', {'active': 'eq.true', 'select': 'id,oddspapi_slug'})
        self.bookmakers = {}
        for book in books:
            slug = book.get('oddspapi_slug')
            if not isinstance(slug, str) or not slug:
                continue
            if slug in self.bookmakers:
                raise TestFailure('ambiguous_bookmaker_mapping')
            self.bookmakers[slug] = book['id']
        if not self.bookmakers:
            raise TestFailure('no_active_bookmakers')
        for table, columns in [('opportunities', 'id,fingerprint,last_seen_at,metadata'),
                               ('opportunity_legs', 'id,opportunity_id'),
                               ('quote_history', ','.join(QUOTE_FIELDS))]:
            self.request('GET', table, {'select': columns, 'limit': 0})
        self.events = self.all_rows('events', {'sport': 'eq.football', 'competition': 'eq.Serie A', 'select': 'id,external_keys'})
        self.market_ids = set()
        self.old_quotes = {}
        self.open_ops = []
        for event in self.events:
            markets = self.all_rows('markets', {'event_id': 'eq.' + str(event['id']), 'select': 'id,normalized_key'})
            for market in markets:
                self.market_ids.add(market['id'])
                self.open_ops.extend(self.all_rows('opportunities', {'market_id': 'eq.' + str(market['id']), 'status': 'eq.open', 'select': 'id,fingerprint,market_id,metadata'}))
                if market['normalized_key'] not in ('1x2', 'ou_2.5', 'ou_3.5'):
                    continue
                quotes = self.all_rows('quotes_current', {'market_id': 'eq.' + str(market['id']), 'source_id': 'eq.' + str(self.source_id), 'select': '*'} )
                for quote in quotes:
                    self.old_quotes[quote_key(quote)] = quote

    def persist_quote(self, row):
        previous = self.old_quotes.get(quote_key(row))
        self.request('POST', 'quotes_current', {'on_conflict': ','.join(KEY_FIELDS)}, row)
        if quote_changed(previous, row):
            self.request('POST', 'quote_history', payload=row)

    def save_snapshot(self, rows, captured):
        seen, fresh_markets = set(), set()
        for event, quotes in rows:
            fixture_id = event['external_keys']['oddspapi']
            existing = [e for e in self.events if e['external_keys'].get('oddspapi') == fixture_id]
            if len(existing) > 1:
                raise TestFailure('ambiguous_existing_event')
            if existing:
                event['external_keys'] = {**existing[0]['external_keys'], **event['external_keys']}
                result = self.request('PATCH', 'events', {'id': 'eq.' + str(existing[0]['id'])}, event)
            else:
                result = self.request('POST', 'events', payload=event)
                self.events.append({'id': result[0]['id'], 'external_keys': event['external_keys']})
            event_id = result[0]['id']
            saved_markets = {}
            for slug, market, quote in quotes:
                key = market['normalized_key']
                if key not in saved_markets:
                    result = self.request('POST', 'markets', {'on_conflict': 'event_id,normalized_key'}, {**market, 'event_id': event_id})
                    saved_markets[key] = result[0]['id']
                mid = saved_markets[key]
                self.market_ids.add(mid)
                fresh_markets.add(mid)
                row = {**quote, 'market_id': mid, 'bookmaker_id': self.bookmakers[slug], 'source_id': self.source_id}
                if quote_key(row) in seen:
                    raise TestFailure('duplicate_snapshot_quote')
                seen.add(quote_key(row))
                self.persist_quote(row)
        # Only this source, these Serie A markets. Other sports/sources are untouched.
        for key, previous in self.old_quotes.items():
            if key not in seen and previous['is_active']:
                row = {field: previous.get(field) for field in QUOTE_FIELDS}
                row.update(is_active=False, received_at=captured.isoformat())
                self.persist_quote(row)
        return len(seen), fresh_markets

    def opportunities(self, fresh_markets, scanned_at):
        candidates = self.request('POST', 'rpc/scan_surebet_candidates', payload={
            'p_min_roi_percent': 2, 'p_capital_limit_eur': 600, 'p_max_age_seconds': 30})
        seen = set()
        for candidate in candidates:
            if candidate['market_id'] not in fresh_markets:
                continue
            legs = candidate['legs']
            # Do not publish candidates using quotes outside the configured acquisition.
            if not legs or any(leg['bookmaker_id'] not in self.bookmakers.values()
                               or leg['source_id'] != self.source_id for leg in legs):
                continue
            fingerprint = candidate['fingerprint']
            if fingerprint in seen:
                raise TestFailure('duplicate_candidate')
            seen.add(fingerprint)
            existing = [op for op in self.open_ops if op['fingerprint'] == fingerprint]
            if len(existing) > 1:
                raise TestFailure('ambiguous_open_opportunity')
            row = {field: candidate[field] for field in OP_FIELDS}
            row.update(status='open', capital_limit_eur=600, last_seen_at=scanned_at,
                       metadata={**(existing[0].get('metadata') or {} if existing else {}),
                                 'scanner': 'serie_a_once', 'tournament_id': 23, 'sport_id': 10,
                                 'normalized_key': candidate['normalized_key'], 'scan_at': scanned_at})
            safe_legs = [{field: leg.get(field) for field in LEG_FIELDS} for leg in legs]
            # Never carry provider URLs or arbitrary candidate payloads into storage.
            encoded = json.dumps([row, safe_legs])
            if any(secret and secret in encoded for secret in (self.settings.key, self.settings.oddspapi_key)):
                raise TestFailure('unsafe_candidate_data')
            if existing:
                op_id = existing[0]['id']
                self.request('PATCH', 'opportunities', {'id': 'eq.' + str(op_id)}, row)
            else:
                result = self.request('POST', 'opportunities', payload=row)
                op_id = result[0]['id']
            try:
                self.request('DELETE', 'opportunity_legs', {'opportunity_id': 'eq.' + str(op_id)})
                self.request('POST', 'opportunity_legs', payload=[{**leg, 'opportunity_id': op_id} for leg in safe_legs])
            except Exception:
                # Best effort: do not leave a partially replaced opportunity open.
                self.request('PATCH', 'opportunities', {'id': 'eq.' + str(op_id)}, {'status': 'stale'})
                raise
        for old in self.open_ops:
            if old['fingerprint'] not in seen:
                self.request('PATCH', 'opportunities', {'id': 'eq.' + str(old['id']), 'status': 'eq.open'}, {'status': 'stale'})
        return len(seen)


def run_scan(client, settings, account_check, logger):
    provider = None
    try:
        if not settings.oddspapi_key:
            raise TestFailure('oddspapi_key_missing')
        account = account_check(client, settings)
        if account['status'] not in ('online', 'degraded'):
            raise TestFailure('account_not_ready')
        if account['metadata']['remaining_requests'] < 2:
            raise TestFailure('insufficient_quota')
        if 10 not in account['metadata']['sport_ids']:
            raise TestFailure('soccer_not_enabled')
        store = ScanStore(client, settings)
        store.prepare()
        provider = ScanProvider(client, settings.oddspapi_key, 2)
        catalog = market_catalog(provider.get('markets', {'language': 'en'}))
        snapshot = provider.get('odds-by-tournaments', {'tournamentIds': '23'})
        if isinstance(snapshot, dict) and 'fixtureId' in snapshot:
            snapshot = [snapshot]
        if not isinstance(snapshot, list):
            raise TestFailure('invalid_snapshot_shape')
        ids = set()
        for fixture in snapshot:
            if not isinstance(fixture, dict) or not isinstance(fixture.get('bookmakerOdds'), dict):
                raise TestFailure('invalid_snapshot_shape')
            fixture_id = fixture.get('fixtureId')
            if not isinstance(fixture_id, str) or not fixture_id or fixture_id in ids:
                raise TestFailure('invalid_or_duplicate_fixture')
            ids.add(fixture_id)
        # Normalize everything before any write or absence-based deactivation.
        captured = datetime.now(timezone.utc)
        rows = normalize(snapshot, '23', catalog, store.bookmakers, captured)
        # Explicit suspended flags, when supplied at any nested level, must win.
        # normalize's active flags already reject inactive data.
        encoded = json.dumps(rows, ensure_ascii=False)
        if any(secret and secret in encoded for secret in (settings.key, settings.oddspapi_key)):
            raise TestFailure('unsafe_normalized_data')
        found = {slug for _, quotes in rows for slug, _, _ in quotes}
        count, fresh_markets = store.save_snapshot(rows, captured)
        opportunities = store.opportunities(fresh_markets, captured.isoformat())
        logger.info('serie_a_scan_ok fixtures_received=%s target_bookmakers_found=%s quotes_saved=%s opportunities_found=%s billable_attempted=%s',
                    len(snapshot), len(found), count, opportunities, provider.used)
        return 0
    except TestFailure as exc:
        logger.error('%s billable_attempted=%s', exc, provider.used if provider else 0)
    except httpx.HTTPError:
        logger.error('storage_http_or_network_error billable_attempted=%s', provider.used if provider else 0)
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, OverflowError):
        logger.error('invalid_response_or_schema billable_attempted=%s', provider.used if provider else 0)
    return 1
