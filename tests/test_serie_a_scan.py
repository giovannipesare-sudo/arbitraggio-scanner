import copy
import contextlib
import io
import json
import logging
import unittest
from unittest.mock import patch

import httpx
import worker
import serie_a_scan as scan
import test_oddspapi_snapshot as snapshot_tests


class ScanTests(unittest.TestCase):
    def setUp(self):
        snapshot_tests.SnapshotTests.setUp(self)
        self.db = {name: [] for name in ('events','markets','quotes_current','quote_history','opportunities','opportunity_legs')}
        self.db['bookmakers'] = [{'id':1,'active':True,'oddspapi_slug':'bet365.it'},
                                 {'id':2,'active':True,'oddspapi_slug':'new-active.it'},
                                 {'id':3,'active':False,'oddspapi_slug':'inactive.it'}]
        self.db['quote_sources'] = [{'id':4,'code':'ODDSPAPI','enabled':True}]
        self.rpc_candidates = []
        self.rpc_calls = 0
        self.fail_path = None
        self.remaining = 10
        self.requests=[]

    def client(self):
        def handler(req):
            self.requests.append(req)
            if req.url.path == self.fail_path:
                return httpx.Response(500,text='PRIVATE-CREDENTIAL')
            if req.url.host == 'api.oddspapi.io':
                self.assertEqual(req.method,'GET')
                if req.url.path == '/v4/account':
                    return httpx.Response(200,json={'subscriptions':[{'is_active':True,'request_limit':10,
                        'request_count':10-self.remaining,'websocket_access':False,'sport_ids':[10],
                        'bookmakers':{'bet365.it':{}}}]})
                if req.url.path == '/v4/markets':
                    self.assertEqual(dict(req.url.params),{'language':'en','apiKey':'PRIVATE-CREDENTIAL'})
                    return httpx.Response(200,json=self.catalog)
                self.assertEqual(req.url.path,'/v4/odds-by-tournaments')
                self.assertEqual(dict(req.url.params),{'tournamentIds':'23','apiKey':'PRIVATE-CREDENTIAL'})
                self.assertEqual(req.extensions['timeout']['read'],30.0)
                return httpx.Response(200,json=self.snapshot)
            self.assertEqual(req.url.host,'example.supabase.co')
            if req.url.path == '/rest/v1/rpc/scan_surebet_candidates':
                self.assertEqual(req.method,'POST')
                self.assertEqual(json.loads(req.content),{'p_min_roi_percent':2,'p_capital_limit_eur':600,'p_max_age_seconds':30})
                self.rpc_calls += 1
                return httpx.Response(200,json=self.rpc_candidates)
            table=req.url.path.rsplit('/',1)[-1]
            self.assertIn(table,self.db)
            params=dict(req.url.params)
            def matches(row):
                for key,value in params.items():
                    if key in ('select','order','offset','limit','on_conflict'): continue
                    self.assertTrue(value.startswith('eq.'))
                    actual=row.get(key)
                    actual=str(actual).lower() if isinstance(actual,bool) else str(actual)
                    if actual != value[3:]: return False
                return True
            matching=[r for r in self.db[table] if matches(r)]
            if req.method=='GET':
                offset=int(params.get('offset',0)); limit=int(params.get('limit',500))
                return httpx.Response(200,json=copy.deepcopy(matching[offset:offset+limit]))
            if req.method=='DELETE':
                self.db[table]=[r for r in self.db[table] if not matches(r)]
                return httpx.Response(204)
            payload=json.loads(req.content)
            if req.method=='PATCH':
                for row in matching: row.update(copy.deepcopy(payload))
                return httpx.Response(200,json=copy.deepcopy(matching))
            self.assertEqual(req.method,'POST')
            output=[]
            for item in (payload if isinstance(payload,list) else [payload]):
                conflict=params.get('on_conflict','').split(',')
                existing=next((r for r in self.db[table] if conflict!=[''] and all(r[k]==item[k] for k in conflict)),None)
                if existing is not None:
                    existing.update(copy.deepcopy(item)); output.append(existing)
                else:
                    row={**copy.deepcopy(item),'id':max([r['id'] for r in self.db[table]]+[0])+1}
                    self.db[table].append(row); output.append(row)
            return httpx.Response(201,json=copy.deepcopy(output))
        return httpx.Client(transport=httpx.MockTransport(handler),timeout=10)

    def run_scan(self):
        with self.client() as client:
            return scan.run_scan(client,self.settings,worker.check_account,logging.getLogger('scan-test'))

    def candidate(self):
        return {'market_id':1,'event_id':1,'event_name':'Home - Away','normalized_key':'1x2',
                'opportunity_type':'surebet_3way','arb_index':0.95,'roi_percent':5,
                'total_committed_eur':599,'expected_return_eur':630,'expected_profit_eur':31,
                'max_quote_age_ms':10,'confidence_label':'B','fingerprint':'fp-current',
                'legs':[{'bookmaker_id':1,'source_id':4,'outcome_code':c,'side':'back','odds':3.2,
                         'suggested_stake_eur':199,'liability_eur':0,'quote_received_at':'2026-09-19T10:00:00Z',
                         'bookmaker_changed_at':'2026-09-19T09:59:59Z'} for c in ('1','X','2')]}

    def test_two_requests_pause_and_dynamic_bookmaker_filter(self):
        book=copy.deepcopy(self.fixture['bookmakerOdds']['bet365.it'])
        self.fixture['bookmakerOdds'].update({'new-active.it':copy.deepcopy(book),'inactive.it':copy.deepcopy(book),'unknown.it':copy.deepcopy(book)})
        self.assertEqual(self.run_scan(),0)
        self.assertEqual([r.url.path for r in self.requests if r.url.host=='api.oddspapi.io'],
                         ['/v4/account','/v4/markets','/v4/odds-by-tournaments'])
        self.assertEqual([c.args for c in self.sleep.call_args_list],[(1.5,)])
        self.assertEqual({q['bookmaker_id'] for q in self.db['quotes_current']},{1,2})
        self.assertEqual(len(self.db['quotes_current']),14)
        self.assertEqual(self.rpc_calls,1)

    def test_unchanged_quotes_do_not_add_history_then_changed_price_and_timestamp_do(self):
        self.assertEqual(self.run_scan(),0)
        self.assertEqual(len(self.db['quote_history']),7)
        self.assertEqual(self.run_scan(),0)
        self.assertEqual(len(self.db['quote_history']),7)
        data=self.fixture['bookmakerOdds']['bet365.it']['markets']['101']['outcomes']['101']['players']['0']
        data['price']=3.2
        self.assertEqual(self.run_scan(),0)
        self.assertEqual(len(self.db['quote_history']),8)
        data['changedAt']='2026-09-19T10:01:00Z'
        self.assertEqual(self.run_scan(),0)
        self.assertEqual(len(self.db['quote_history']),9)

    def test_missing_and_suspended_quotes_inactive_history_once(self):
        self.assertEqual(self.run_scan(),0)
        self.fixture['bookmakerOdds']['bet365.it']['markets']['101']['outcomes']['101']['suspended']=True
        self.assertEqual(self.run_scan(),0)
        inactive=[q for q in self.db['quotes_current'] if not q['is_active']]
        self.assertEqual(len(inactive),1)
        self.assertEqual(len(self.db['quote_history']),8)
        self.assertEqual(self.run_scan(),0)
        self.assertEqual(len(self.db['quote_history']),8)
        self.snapshot=[]
        self.assertEqual(self.run_scan(),0)
        self.assertTrue(all(not q['is_active'] for q in self.db['quotes_current']))
        self.assertEqual(len(self.db['quote_history']),14)

    def test_create_update_replace_legs_and_stale_unseen(self):
        self.rpc_candidates=[self.candidate()]
        self.assertEqual(self.run_scan(),0)
        self.assertEqual(len(self.db['opportunities']),1)
        self.assertEqual(len(self.db['opportunity_legs']),3)
        self.assertEqual(self.db['opportunities'][0]['status'],'open')
        self.rpc_candidates[0]['roi_percent']=6
        self.rpc_candidates[0]['legs'][0]['suggested_stake_eur']=200
        self.assertEqual(self.run_scan(),0)
        self.assertEqual(len(self.db['opportunities']),1)
        self.assertEqual(len(self.db['opportunity_legs']),3)
        self.assertEqual(self.db['opportunities'][0]['roi_percent'],6)
        self.assertIn('last_seen_at',self.db['opportunities'][0])
        self.rpc_candidates=[]
        self.assertEqual(self.run_scan(),0)
        self.assertEqual(self.db['opportunities'][0]['status'],'stale')

    def test_other_competitions_sources_and_candidates_untouched(self):
        self.db['events']=[{'id':99,'sport':'football','competition':'Other','external_keys':{}}]
        self.db['markets']=[{'id':99,'event_id':99,'normalized_key':'1x2'}]
        self.db['opportunities']=[{'id':99,'market_id':99,'fingerprint':'other','status':'open'}]
        self.rpc_candidates=[{**self.candidate(),'market_id':99,'fingerprint':'other'}]
        self.assertEqual(self.run_scan(),0)
        self.assertEqual(self.db['opportunities'][0]['status'],'open')
        self.assertEqual(len(self.db['opportunities']),1)

    def test_failed_snapshot_no_writes_rpc_or_staling(self):
        self.fail_path='/v4/odds-by-tournaments'
        with self.assertLogs('scan-test') as logs: self.assertEqual(self.run_scan(),1)
        self.assertEqual(self.rpc_calls,0)
        self.assertFalse(self.db['events'])
        self.assertNotIn('PRIVATE-CREDENTIAL',str(logs.output))
        self.assertEqual(sum(r.url.path==self.fail_path for r in self.requests),1)

    def test_insufficient_quota_no_billable(self):
        self.remaining=1
        with self.assertLogs('scan-test'): self.assertEqual(self.run_scan(),1)
        self.assertEqual(len(self.requests),1)

    def test_live_past_and_suspended_are_excluded(self):
        for change in ({'statusId':2},{'startTime':'2000-01-01T00:00:00Z'},{'trueStartTime':'2026-09-19T10:00:00Z'}):
            original=copy.deepcopy(self.fixture)
            self.fixture.update(change)
            self.assertEqual(self.run_scan(),0)
            self.assertFalse(self.db['quotes_current'])
            self.fixture.clear(); self.fixture.update(original)
        for market in self.fixture['bookmakerOdds']['bet365.it']['markets'].values(): market['suspended']=True
        self.assertEqual(self.run_scan(),0)
        self.assertFalse(self.db['quotes_current'])

    def test_invalid_snapshot_does_not_deactivate(self):
        self.assertEqual(self.run_scan(),0)
        del self.fixture['bookmakerOdds']
        with self.assertLogs('scan-test'): self.assertEqual(self.run_scan(),1)
        self.assertTrue(all(q['is_active'] for q in self.db['quotes_current']))
        self.assertEqual(self.rpc_calls,1)

    def test_leg_write_failure_marks_opportunity_stale(self):
        self.rpc_candidates=[self.candidate()]
        self.fail_path='/rest/v1/opportunity_legs'
        # Preflight GET must succeed; inject failure only after preflight.
        original=self.client
        def failing_client():
            self.fail_path=None
            client=original()
            original_request=client.request
            def request(method,url,**kwargs):
                if method=='POST' and url.endswith('/opportunity_legs'):
                    return httpx.Response(500,request=httpx.Request(method,url))
                return original_request(method,url,**kwargs)
            client.request=request
            return client
        with patch.object(self,'client',side_effect=failing_client), self.assertLogs('scan-test'):
            self.assertEqual(self.run_scan(),1)
        self.assertEqual(self.db['opportunities'][0]['status'],'stale')

    def test_hard_two_request_cap_and_no_tournament_discovery(self):
        with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200,json=[]))) as client:
            provider=scan.ScanProvider(client,'test',100)
            provider.get('markets',{'language':'en'})
            provider.get('odds-by-tournaments',{'tournamentIds':'23'})
            with self.assertRaises(scan.TestFailure): provider.get('markets',{})
            with self.assertRaises(scan.TestFailure): provider.get('tournaments',{})
            self.assertEqual(provider.used,2)

    def test_reappearing_quote_history_and_other_source_preserved(self):
        self.assertEqual(self.run_scan(),0)
        other={**self.db['quotes_current'][0],'id':100,'source_id':99}
        self.db['quotes_current'].append(other)
        original=self.snapshot
        self.snapshot=[]
        self.assertEqual(self.run_scan(),0)
        self.assertTrue(other['is_active'])
        self.snapshot=original
        self.assertEqual(self.run_scan(),0)
        self.assertEqual(len(self.db['quote_history']),21)

    def test_rpc_failure_does_not_stale_unseen(self):
        self.rpc_candidates=[self.candidate()]
        self.assertEqual(self.run_scan(),0)
        self.fail_path='/rest/v1/rpc/scan_surebet_candidates'
        with self.assertLogs('scan-test'): self.assertEqual(self.run_scan(),1)
        self.assertEqual(self.db['opportunities'][0]['status'],'open')

    def test_cli_exclusivity_and_no_threads(self):
        for other in ('--once','--oddspapi-test','--oddspapi-snapshot-probe'):
            with patch('sys.argv',['worker.py','--scan-serie-a-once',other]), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                worker.main()
        client=self.client()
        with patch('sys.argv',['worker.py','--scan-serie-a-once']), patch.object(worker.Settings,'from_env',return_value=self.settings), \
             patch.object(worker.httpx,'Client',return_value=client), patch.object(worker.threading,'Thread') as thread:
            self.assertEqual(worker.main(),0)
            thread.assert_not_called()


if __name__=='__main__': unittest.main()
