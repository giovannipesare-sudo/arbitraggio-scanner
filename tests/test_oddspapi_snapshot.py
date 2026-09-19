import copy
import json
import logging
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import httpx
import worker
import oddspapi_test as shot


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        sleeper = patch.object(shot.time, 'sleep')
        self.sleep = sleeper.start()
        self.addCleanup(sleeper.stop)
        self.settings = worker.Settings('https://example.supabase.co', 'sb_secret_test', oddspapi_key='PRIVATE-CREDENTIAL')
        self.requests, self.saved = [], []
        self.remaining = 10
        self.fail = None
        self.fail_status = 500
        self.network_error = None
        self.catalog = []
        for mid, kind, line, names in [(101,'1x2',0,['1','X','2']), (110,'totals',2.5,['Over','Under']), (112,'totals',3.5,['Over','Under'])]:
            self.catalog.append({'marketId':mid,'marketType':kind,'handicap':line,'period':'fulltime',
                                 'sportId':10,'playerProp':False,
                                 'outcomes':[{'outcomeId':mid+i,'outcomeName':n} for i,n in enumerate(names)]})
        self.fixture = {'fixtureId':'f123','sportId':10,'tournamentId':23,'statusId':0,
                        'startTime':'2099-01-01T12:00:00Z','participant1Name':'Home','participant2Name':'Away',
                        'bookmakerOdds':{'bet365.it':{'bookmakerIsActive':True,'suspended':False,'markets':{}}}}
        for market in self.catalog:
            outcomes = {str(o['outcomeId']):{'players':{'0':{'active':True,'price':2.1,
                        'bookmakerChangedAt':'2026-09-19T10:00:00Z','changedAt':'2026-09-19T10:00:01Z'}}}
                        for o in market['outcomes']}
            self.fixture['bookmakerOdds']['bet365.it']['markets'][str(market['marketId'])] = {'marketActive':True,'outcomes':outcomes}
        self.snapshot = [self.fixture]
        self.existing = False

    def client(self):
        def handler(req):
            self.requests.append(req)
            if req.url.host == 'api.oddspapi.io':
                self.assertEqual(req.method,'GET')
                self.assertEqual(req.url.params['apiKey'],'PRIVATE-CREDENTIAL')
                path = req.url.path
                self.assertIn(path, ['/v4/account','/v4/tournaments','/v4/markets','/v4/odds-by-tournaments'])
                if path == self.fail:
                    if self.network_error:
                        raise self.network_error('PRIVATE-CREDENTIAL https://secret.example/?apiKey=secret', request=req)
                    return httpx.Response(self.fail_status,text='RAW-BODY PRIVATE-CREDENTIAL',
                                          headers={'X-Sensitive':'SECRET-HEADER'})
                if path == '/v4/account':
                    return httpx.Response(200,json={'api_key':'PRIVATE-CREDENTIAL','subscriptions':[
                        {'is_active':True,'request_limit':100,'request_count':100-self.remaining,
                         'websocket_access':False,'sport_ids':[10], 'bookmakers':{s:{} for s in shot.BOOKMAKERS}}]})
                if path == '/v4/tournaments':
                    return httpx.Response(200,json=[{'tournamentId':99,'tournamentName':'Serie A','categorySlug':'brazil'},
                        {'tournamentId':23,'tournamentName':'Serie A','categorySlug':'italy'}])
                if path == '/v4/markets': return httpx.Response(200,json=self.catalog)
                if path == '/v4/odds-by-tournaments':
                    self.assertEqual(req.url.params['tournamentIds'],'23')
                    self.assertEqual(set(req.url.params['bookmakers'].split(',')),set(shot.BOOKMAKERS))
                    return httpx.Response(200,json=self.snapshot)
            self.assertEqual(req.url.host,'example.supabase.co')
            table = req.url.path.rsplit('/',1)[-1]
            if req.method == 'GET':
                if table == 'quote_sources': return httpx.Response(200,json=[{'id':4}])
                if table == 'bookmakers': return httpx.Response(200,json=[{'id':i+1,'oddspapi_slug':s} for i,s in enumerate(shot.BOOKMAKERS)])
                if table == 'events' and self.existing and 'limit' not in req.url.params:
                    return httpx.Response(200,json=[{'id':20,'external_keys':{'other':'keep','oddspapi':'f123'}}])
                return httpx.Response(200,json=[])
            body = json.loads(req.content)
            self.assertNotIn('PRIVATE-CREDENTIAL',json.dumps(body))
            self.saved.append((table,body,req.method))
            return httpx.Response(201,json=[{**body,'id':20}])
        return httpx.Client(transport=httpx.MockTransport(handler), timeout=httpx.Timeout(10.0, connect=5.0))

    def run_shot(self):
        with self.client() as client:
            return shot.run_test(client,self.settings,worker.check_account,logging.getLogger('test-shot'))

    def test_end_to_end_three_billable_and_all_markets(self):
        self.assertEqual(self.run_shot(),0)
        provider = [r.url.path for r in self.requests if r.url.host == 'api.oddspapi.io']
        self.assertEqual(provider,['/v4/account','/v4/tournaments','/v4/markets','/v4/odds-by-tournaments'])
        self.assertEqual(len([s for s in self.saved if s[0]=='events']),1)
        self.assertEqual(len([s for s in self.saved if s[0]=='markets']),3)
        quotes = [s[1] for s in self.saved if s[0]=='quotes_current']
        self.assertEqual(len(quotes),7)
        self.assertEqual(len([s for s in self.saved if s[0]=='quote_history']),7)
        self.assertEqual(quotes[0]['bookmaker_changed_at'],'2026-09-19T10:00:00+00:00')
        self.assertEqual(json.loads(quotes[0]['raw_ref'])['changedAt'],'2026-09-19T10:00:01+00:00')
        self.assertTrue(all(q['side']=='back' for q in quotes))

    def test_quota_insufficient_stops_before_billable(self):
        self.remaining = 2
        with self.assertLogs('test-shot') as logs: self.assertEqual(self.run_shot(),1)
        self.assertEqual(len(self.requests),1)
        self.assertFalse(self.saved)
        self.assertIn('insufficient_quota billable_attempted=0',str(logs.output))
        self.sleep.assert_not_called()

    def test_no_key_no_requests(self):
        self.settings = worker.Settings(self.settings.url,self.settings.key)
        with self.assertLogs('test-shot'): self.assertEqual(self.run_shot(),1)
        self.assertEqual(self.requests,[])

    def test_http_failure_no_retries_or_secret_logs(self):
        self.fail = '/v4/markets'
        with self.assertLogs('test-shot') as logs: self.assertEqual(self.run_shot(),1)
        self.assertNotIn('PRIVATE-CREDENTIAL',str(logs.output))
        self.assertEqual(sum(r.url.path=='/v4/markets' for r in self.requests),1)
        self.assertFalse(any(r.url.path=='/v4/odds-by-tournaments' for r in self.requests))

    def test_filter_live_past_started_and_wrong_tournament(self):
        self.snapshot = []
        for change in ({'statusId':2},{'statusId':3},{'statusId':None},{'statusId':True},
                       {'startTime':'2000-01-01T00:00:00Z'}, {'trueStartTime':'2026-09-19T10:00:00Z'},
                       {'trueEndTime':'2026-09-19T10:00:00Z'},{'tournamentId':99},{'sportId':11}):
            row=copy.deepcopy(self.fixture); row.update(change); self.snapshot.append(row)
        self.assertEqual(self.run_shot(),0)
        self.assertFalse(self.saved)

    def test_disallowed_suspended_and_invalid_quotes(self):
        self.fixture['bookmakerOdds']['unlisted'] = copy.deepcopy(self.fixture['bookmakerOdds']['bet365.it'])
        self.fixture['bookmakerOdds']['bet365.it']['suspended'] = True
        self.assertEqual(self.run_shot(),0)
        self.assertFalse(self.saved)
        self.fixture['bookmakerOdds']['bet365.it']['suspended'] = False
        for m in self.fixture['bookmakerOdds']['bet365.it']['markets'].values():
            for outcome in m['outcomes'].values(): outcome['players']['0']['price']=1
        self.assertEqual(self.run_shot(),0)
        self.assertFalse(self.saved)

    def test_existing_event_is_updated_preserving_other_keys(self):
        self.existing=True
        self.assertEqual(self.run_shot(),0)
        events=[s for s in self.saved if s[0]=='events']
        self.assertEqual(events[0][2],'PATCH')
        self.assertEqual(events[0][1]['external_keys']['other'],'keep')

    def test_missing_market_or_ambiguous_tournament_fails_closed(self):
        with self.assertRaises(shot.TestFailure): shot.market_catalog(self.catalog[:2])
        with self.assertRaises(shot.TestFailure): shot.serie_a([])
        with self.assertRaises(shot.TestFailure): shot.serie_a([{'tournamentId':1,'categorySlug':'italy','tournamentName':'Serie A'}]*2)

    def test_budget_and_duplicate_guard_count_failed_attempts(self):
        with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))) as client:
            p=shot.Provider(client,'test',10)
            p.used=3
            with self.assertRaises(shot.TestFailure): p.get('markets',{})
            self.assertEqual(p.used,4)
            with self.assertRaises(shot.TestFailure): p.get('tournaments',{})
            p=shot.Provider(client,'test',10)
            with self.assertRaises(shot.TestFailure): p.get('markets',{})
            with self.assertRaises(shot.TestFailure): p.get('markets',{})
            with self.assertRaises(shot.TestFailure): p.get('odds',{})
            self.assertEqual(p.used,1)

    def test_http_error_codes_and_attempt_counts(self):
        for attempt, endpoint in enumerate(('tournaments','markets','odds-by-tournaments'), 1):
            for status in (400,403,429,500):
                with self.subTest(endpoint=endpoint,status=status):
                    self.requests.clear(); self.saved.clear(); self.sleep.reset_mock()
                    self.fail = '/v4/' + endpoint
                    self.fail_status = status
                    with self.assertLogs('test-shot') as logs:
                        self.assertEqual(self.run_shot(),1)
                    self.assertEqual(logs.output, [f"ERROR:test-shot:{endpoint.replace('-', '_')}_http_{status} billable_attempted={attempt}"])
                    billable = [r for r in self.requests if r.url.host=='api.oddspapi.io' and r.url.path!='/v4/account']
                    self.assertEqual(len(billable),attempt)
                    self.assertEqual(self.sleep.call_count,attempt-1)
                    self.assertFalse(self.saved)

    def test_network_error_codes_and_attempt_counts(self):
        for attempt, endpoint in enumerate(('tournaments','markets','odds-by-tournaments'), 1):
            for error in (httpx.ConnectTimeout,httpx.ReadTimeout,httpx.WriteTimeout,
                          httpx.PoolTimeout,httpx.ConnectError,httpx.ReadError):
                with self.subTest(endpoint=endpoint,error=error):
                    self.requests.clear(); self.sleep.reset_mock()
                    self.fail = '/v4/' + endpoint
                    self.network_error = error
                    with self.assertLogs('test-shot') as logs:
                        self.assertEqual(self.run_shot(),1)
                    self.assertEqual(logs.output, [f"ERROR:test-shot:{endpoint.replace('-', '_')}_network_error billable_attempted={attempt}"])
                    self.assertEqual(sum(r.url.host=='api.oddspapi.io' and r.url.path!='/v4/account' for r in self.requests),attempt)
                    self.assertEqual(self.sleep.call_count,attempt-1)

    def test_timeout_override_only_for_snapshot(self):
        self.assertEqual(self.run_shot(),0)
        for req in self.requests:
            expected = {'connect':5.0,'read':10.0,'write':10.0,'pool':10.0}
            if req.url.host=='api.oddspapi.io' and req.url.path=='/v4/odds-by-tournaments':
                expected = dict.fromkeys(expected,30.0)
            self.assertEqual(req.extensions['timeout'],expected)
        self.assertEqual([c.args for c in self.sleep.call_args_list],[(1.5,),(1.5,)])

    def test_pause_occurs_after_previous_response_before_next_request(self):
        timeline = []
        def handler(req):
            timeline.append(('request',req.url.path))
            return httpx.Response(200,json=[])
        self.sleep.side_effect = lambda seconds: timeline.append(('pause',seconds))
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            provider=shot.Provider(client,'test',4)
            for endpoint in ('tournaments','markets','odds-by-tournaments'):
                provider.get(endpoint,{})
            with self.assertRaises(shot.TestFailure): provider.get('odds-by-tournaments',{})
        self.assertEqual(timeline,[('request','/v4/tournaments'),('pause',1.5),
                                  ('request','/v4/markets'),('pause',1.5),
                                  ('request','/v4/odds-by-tournaments')])

    def test_secret_in_normalized_name_is_blocked(self):
        self.fixture['participant1Name']='PRIVATE-CREDENTIAL'
        with self.assertLogs('test-shot') as logs: self.assertEqual(self.run_shot(),1)
        self.assertFalse(self.saved)
        self.assertNotIn('PRIVATE-CREDENTIAL',str(logs.output))

    def test_normal_worker_once_never_calls_billable(self):
        client = self.client()
        with patch('sys.argv',['worker.py','--once']), \
             patch.object(worker.Settings,'from_env',return_value=self.settings), \
             patch.object(worker.httpx,'Client',return_value=client):
            self.assertEqual(worker.main(),0)
        self.assertEqual([r.url.path for r in self.requests if r.url.host=='api.oddspapi.io'],['/v4/account'])

    def test_cli_explicit_test_exits_without_starting_threads(self):
        client = self.client()
        with patch('sys.argv',['worker.py','--oddspapi-test']), \
             patch.object(worker.Settings,'from_env',return_value=self.settings), \
             patch.object(worker.httpx,'Client',return_value=client), patch.object(worker,'run') as run, \
             patch.object(worker,'run_source') as source:
            self.assertEqual(worker.main(),0)
            run.assert_not_called(); source.assert_not_called()


if __name__=='__main__': unittest.main()
