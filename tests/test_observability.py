import json
import threading
import unittest
import urllib.request
import urllib.error
from level1_core.server import Server
from level1_core.runtime import Core
from level1_core.observability import LOGGER

class ObservabilityTests(unittest.TestCase):
    def setUp(self):
        self.server=Server(('127.0.0.1',0),Core(),dev=True)
        self.thread=threading.Thread(target=self.server.serve_forever);self.thread.start()
        self.url=f'http://127.0.0.1:{self.server.server_port}'
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join()
    def test_health_readiness_correlation_log(self):
        with urllib.request.urlopen(self.url+'/v1/health') as response:self.assertTrue(json.load(response)['alive'])
        with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(self.url+'/v1/readiness')
        self.assertEqual(error.exception.code,503)
        self.server.core.attach('mock')
        body={'api_version':'1.0','algorithm':'mock','data':'DO_NOT_LOG_SECRET'}
        request=urllib.request.Request(self.url+'/v1/tasks',data=json.dumps(body).encode(),headers={'Content-Type':'application/json','X-Correlation-ID':'e2e-123'})
        with self.assertLogs(LOGGER,level='INFO') as logs:
            with urllib.request.urlopen(request) as response:
                self.assertEqual(response.headers['X-Correlation-ID'],'e2e-123');response.read()
            # log happens after response: synchronize with worker completion via a small event-free join condition
            import time
            deadline=time.monotonic()+1
            while not logs.output and time.monotonic()<deadline:time.sleep(.001)
        self.assertNotIn('DO_NOT_LOG_SECRET',' '.join(logs.output))
        fields=next(json.loads(record.message) for record in logs.records if 'e2e-123' in record.message)
        self.assertEqual(fields['correlation_id'],'e2e-123')
        self.assertEqual(fields['status'],200)
        with urllib.request.urlopen(self.url+'/v1/readiness') as response:self.assertTrue(json.load(response)['ready'])
