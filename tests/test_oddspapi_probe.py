import contextlib
import io
import logging
import unittest
from unittest.mock import patch

import httpx
import worker
from oddspapi_probe import run_probe


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.settings = worker.Settings('https://unused.supabase.co', 'sb_secret_dummy', oddspapi_key='PRIVATE-KEY')
        self.subscription = {'is_active':True, 'request_limit':10, 'request_count':9,
                             'sport_ids':[10], 'bookmakers':{'bet365.it':{}}, 'websocket_access':False}
        self.requests=[]
        self.status=200
        self.account_status=200
        self.network_error=None

    def client(self):
        def handle(req):
            self.requests.append(req)
            self.assertEqual(req.method, 'GET')
            self.assertEqual(req.url.host, 'api.oddspapi.io')  # Supabase is forbidden.
            if req.url.path == '/v4/account':
                self.assertEqual(dict(req.url.params), {'apiKey':'PRIVATE-KEY'})
                self.assertEqual(req.extensions['timeout']['read'],10.0)
                return httpx.Response(self.account_status, json={'api_key':'RETURNED-KEY','subscriptions':[self.subscription]})
            self.assertEqual(req.url.path, '/v4/odds-by-tournaments')
            self.assertEqual(dict(req.url.params), {'tournamentIds':'23','bookmaker':'bet365.it','apiKey':'PRIVATE-KEY'})
            self.assertEqual(req.extensions['timeout'],dict.fromkeys(('connect','read','write','pool'),30.0))
            if self.network_error:
                raise self.network_error('PRIVATE-KEY https://secret.invalid/?apiKey=PRIVATE-KEY',request=req)
            return httpx.Response(self.status,text='RAW-BODY PRIVATE-KEY',
                                  headers={'Location':'https://secret.invalid/?apiKey=PRIVATE-KEY','X-Secret':'PRIVATE-HEADER'})
        return httpx.Client(transport=httpx.MockTransport(handle),timeout=httpx.Timeout(10.0,connect=5.0))

    def run_local(self):
        with self.client() as client, self.assertLogs('probe',level='INFO') as logs:
            code=run_probe(client,self.settings,worker.check_account,logging.getLogger('probe'))
        return code,logs.output

    def test_success_exact_parameters_one_billable_no_pause_no_save(self):
        with patch('time.sleep') as sleep:
            code,logs=self.run_local()
            sleep.assert_not_called()
        self.assertEqual(code,0)
        self.assertEqual(logs,['INFO:probe:snapshot_probe_ok billable_attempted=1'])
        self.assertEqual([r.url.path for r in self.requests],['/v4/account','/v4/odds-by-tournaments'])

    def test_http_errors_no_retry_no_redirect_no_secret_logs(self):
        for status in (302,400,403,429,500):
            with self.subTest(status=status):
                self.requests.clear(); self.status=status
                code,logs=self.run_local()
                self.assertEqual(code,1)
                self.assertEqual(logs,[f'ERROR:probe:odds_by_tournaments_http_{status} billable_attempted=1'])
                self.assertEqual(len(self.requests),2)

    def test_network_error_one_attempt(self):
        for error in (httpx.ReadTimeout,httpx.ConnectTimeout,httpx.ConnectError):
            with self.subTest(error=error):
                self.requests.clear(); self.network_error=error
                code,logs=self.run_local()
                self.assertEqual(code,1)
                self.assertEqual(logs,['ERROR:probe:odds_by_tournaments_network_error billable_attempted=1'])
                self.assertEqual(len(self.requests),2)

    def test_preflight_blocks_before_billable(self):
        for field,value,expected in [('request_count',10,'insufficient_quota'),
                                     ('sport_ids',[11],'soccer_not_enabled'),
                                     ('bookmakers',{'other':{}},'bet365_not_enabled'),
                                     ('is_active',False,'account_not_ready')]:
            with self.subTest(field=field):
                old=self.subscription[field]
                self.subscription[field]=value; self.requests.clear()
                code,logs=self.run_local()
                self.assertEqual(code,1)
                self.assertEqual(logs,[f'ERROR:probe:{expected} billable_attempted=0'])
                self.assertEqual(len(self.requests),1)
                self.subscription[field]=old

    def test_missing_key_and_failed_account(self):
        self.account_status=403
        code,logs=self.run_local()
        self.assertEqual(code,1)
        self.assertEqual(logs,['ERROR:probe:account_not_ready billable_attempted=0'])
        self.assertEqual(len(self.requests),1)
        self.requests.clear()
        self.settings=worker.Settings(self.settings.url,self.settings.key)
        code,logs=self.run_local()
        self.assertEqual(code,1)
        self.assertEqual(self.requests,[])

    def test_cli_no_worker_threads_or_supabase_writes(self):
        client=self.client()
        with patch('sys.argv',['worker.py','--oddspapi-snapshot-probe']), \
             patch.object(worker.Settings,'from_env',return_value=self.settings), \
             patch.object(worker.httpx,'Client',return_value=client), \
             patch.object(worker.threading,'Thread') as thread, \
             patch.object(worker,'send_heartbeat') as heartbeat, \
             patch.object(worker,'update_source_status') as status, \
             patch.object(worker,'run') as run, patch.object(worker,'run_source') as source:
            self.assertEqual(worker.main(),0)
            for action in (thread,heartbeat,status,run,source): action.assert_not_called()
        self.assertEqual(len(self.requests),2)

    def test_cli_mutual_exclusion(self):
        for other in ('--once','--oddspapi-test'):
            with self.subTest(other=other), \
                 patch('sys.argv',['worker.py','--oddspapi-snapshot-probe',other]), \
                 patch.object(worker.httpx,'Client') as client, \
                 contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
                worker.main()
            self.assertEqual(exc.exception.code,2)
            client.assert_not_called()


if __name__=='__main__': unittest.main()
