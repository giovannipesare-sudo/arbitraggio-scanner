import copy
import json
import os
import threading
import unittest
from unittest.mock import patch

import httpx
import worker


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.settings = worker.Settings('https://example.supabase.co', 'sb_secret_test', oddspapi_key='PRIVATE-CREDENTIAL')
        self.account = {'api_key': 'RETURNED-SECRET', 'current_subscription_id': 'current', 'subscriptions': [
            {'subscription_id': 'old', 'is_active': False},
            {'subscription_id': 'current', 'is_active': True, 'request_limit': 100,
             'request_count': 20, 'websocket_access': 0, 'sport_ids': [10, 11],
             'bookmakers': {'pinnacle': {'api_key': 'NESTED-SECRET'}, 'bet365': {}}}]}
        self.requests = []

    def client(self, data=None, status=200, error=None, save_status=201):
        def handler(request):
            self.requests.append(request)
            if request.url.host == 'api.oddspapi.io':
                self.assertEqual(request.method, 'GET')
                self.assertEqual(request.url.path, '/v4/account')
                self.assertEqual(request.url.params['apiKey'], self.settings.oddspapi_key)
                self.assertNotIn('apikey', request.headers)
                if error: raise error('PRIVATE-CREDENTIAL', request=request)
                if isinstance(data, str): return httpx.Response(status, text=data)
                return httpx.Response(status, json=self.account if data is None else data,
                                      headers={'Location': 'https://api.oddspapi.io/v4/odds'})
            self.assertEqual(request.url.host, 'example.supabase.co')
            self.assertEqual(request.method, 'POST')
            self.assertIn(request.url.path, ['/rest/v1/scanner_status', '/rest/v1/worker_heartbeats'])
            return httpx.Response(save_status)
        return httpx.Client(transport=httpx.MockTransport(handler))

    def test_online_upsert_allowlist(self):
        with self.client() as client, self.assertLogs('worker') as logs:
            self.assertTrue(worker.update_source_status(client, self.settings))
        request = self.requests[-1]
        self.assertEqual(request.url.params['on_conflict'], 'component_key')
        body = json.loads(request.content)
        self.assertEqual(body['component_key'], 'oddspapi')
        self.assertEqual(body['component_type'], 'source')
        self.assertEqual(body['status'], 'online')
        self.assertIsNone(body['last_error'])
        self.assertIn('last_success_at', body)
        self.assertIn('last_heartbeat_at', body)
        self.assertEqual(body['metadata'], {'request_limit': 100, 'request_count': 20,
            'remaining_requests': 80, 'websocket_access': 0, 'sport_ids': [10, 11],
            'bookmaker_count': 2, 'bookmaker_slugs': ['bet365', 'pinnacle']})
        for secret in ('PRIVATE-CREDENTIAL', 'RETURNED-SECRET', 'NESTED-SECRET', 'api_key'):
            self.assertNotIn(secret, request.content.decode() + str(logs.output))

    def test_disabled_no_odds_request(self):
        settings = worker.Settings(self.settings.url, self.settings.key)
        with self.client() as client:
            self.assertTrue(worker.update_source_status(client, settings))
        self.assertEqual(len(self.requests), 1)
        body = json.loads(self.requests[0].content)
        self.assertEqual(body['status'], 'disabled')
        self.assertNotIn('last_success_at', body)
        self.assertIsNone(body['last_error'])

    def test_quota_boundary_and_exhaustion(self):
        for count, status, remaining in [(89,'online',11),(90,'degraded',10),(100,'degraded',0),(120,'degraded',0)]:
            with self.subTest(count=count):
                self.account['subscriptions'][1]['request_count'] = count
                with self.client() as client: body = worker.check_account(client, self.settings)
                self.assertEqual(body['status'], status)
                self.assertEqual(body['metadata']['remaining_requests'], remaining)

    def test_errors_preserve_previous_success(self):
        for status in (302, 401, 403, 429, 500):
            with self.subTest(status=status), self.client(status=status) as client:
                body = worker.check_account(client, self.settings)
                self.assertEqual(body['status'], 'offline')
                self.assertNotIn('last_success_at', body)
                self.assertEqual(body['last_error'], f'account_http_{status}')
        for error in (httpx.ReadTimeout, httpx.ConnectError):
            with self.client(error=error) as client:
                body = worker.check_account(client, self.settings)
                self.assertEqual(body['last_error'], 'account_network_error')
                self.assertNotIn('last_success_at', body)

    def test_invalid_json_no_active_or_bad_fields(self):
        cases = ['not JSON PRIVATE-CREDENTIAL', [], {}, {'subscriptions': []}]
        for key, value in [('request_limit',None), ('request_count',-1), ('websocket_access','yes'), ('sport_ids',['secret']), ('bookmakers',[]), ('bookmakers',{'PRIVATE-CREDENTIAL':{}})]:
            data = copy.deepcopy(self.account)
            data['subscriptions'][1][key] = value
            cases.append(data)
        for data in cases:
            with self.subTest(data=data), self.client(data=data) as client:
                body = worker.check_account(client, self.settings)
                self.assertEqual(body['status'], 'offline')
                self.assertEqual(body['metadata'], {})
                self.assertNotIn('PRIVATE-CREDENTIAL', json.dumps(body))

    def test_success_clears_error_after_recovery(self):
        with self.client(status=500) as client: failed = worker.check_account(client, self.settings)
        with self.client() as client: recovered = worker.check_account(client, self.settings)
        self.assertIsNotNone(failed['last_error'])
        self.assertIsNone(recovered['last_error'])
        self.assertIn('last_success_at', recovered)

    def test_supabase_failure_does_not_break_heartbeat(self):
        with self.client(save_status=503) as client:
            self.assertFalse(worker.update_source_status(client, self.settings))
        with self.client() as client:
            self.assertTrue(worker.send_heartbeat(client, self.settings))
        self.assertEqual(self.requests[-1].url.path, '/rest/v1/worker_heartbeats')

    def test_five_minute_cadence(self):
        class Stop:
            done = False
            waits = []
            def is_set(self): return self.done
            def wait(self, seconds):
                self.waits.append(seconds)
                self.done = len(self.waits) == 2
        stop = Stop()
        with self.client() as client, patch.object(worker.time, 'monotonic', side_effect=[0,2,300,302]):
            worker.run_source(client, self.settings, stop)
        self.assertEqual(stop.waits, [298,298])
        self.assertEqual(sum(r.url.host == 'api.oddspapi.io' for r in self.requests), 2)
        self.assertEqual(worker.HEARTBEAT_INTERVAL_SECONDS, 15)

    def test_slow_account_does_not_block_heartbeat(self):
        entered, release, stop = threading.Event(), threading.Event(), threading.Event()
        heartbeats = []
        def handler(request):
            if request.url.host == 'api.oddspapi.io':
                entered.set()
                if not release.wait(2): raise AssertionError('Heartbeat blocked')
                return httpx.Response(200, json=self.account)
            if request.url.path == '/rest/v1/worker_heartbeats':
                heartbeats.append(request)
                if len(heartbeats) == 2:
                    stop.set()
                    release.set()
            return httpx.Response(201)
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            source = threading.Thread(target=worker.run_source, args=(client, self.settings, stop))
            source.start()
            try:
                self.assertTrue(entered.wait(1))
                with patch.object(worker, 'HEARTBEAT_INTERVAL_SECONDS', 0.01):
                    worker.run(client, self.settings, stop)
            finally:
                release.set()
                stop.set()
                source.join(2)
            self.assertFalse(source.is_alive())
        self.assertEqual(len(heartbeats), 2)

    def test_optional_env_and_test_mode(self):
        env = {'SUPABASE_URL':self.settings.url, 'SUPABASE_SECRET_KEY':self.settings.key}
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(worker.Settings.from_env().oddspapi_key, '')
            os.environ['ODDSPAPI_API_KEY'] = 'PRIVATE-CREDENTIAL'
            self.assertNotIn('PRIVATE-CREDENTIAL', repr(worker.Settings.from_env()))
            os.environ['WORKER_MODE'] = 'live'
            with self.assertRaises(ValueError): worker.Settings.from_env()


if __name__ == '__main__':
    unittest.main()
