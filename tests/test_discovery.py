import threading
import unittest
from level1_core.discovery import Discovery
from level1_core.transport import origin
class DiscoveryTests(unittest.TestCase):
    def test_wait_and_reconnect(self):
        class Client:
            fails=True
            def request(self,*args):
                if self.fails:raise OSError()
                return dict(instance='node',mode='running')
        client=Client();discovery=Discovery(client,['https://localhost'])
        self.assertEqual(discovery.probe_once(),[])
        self.assertEqual(discovery.status()['state'],'waiting_for_allowed_server')
        client.fails=False
        self.assertEqual(len(discovery.probe_once()),1)
    def test_stop_and_backoff(self):
        class Client:
            def request(self,*args):raise OSError()
        stop=threading.Event();reports=[]
        def callback(report):reports.append(report);stop.set()
        Discovery(Client(),['https://localhost'],.01,.02).run(stop,callback)
        self.assertTrue(0<reports[0]['next_retry_s']<=.02)
    def test_url_policy(self):
        for url in ('http://localhost','https://user:pass@localhost','https://localhost/path','https://localhost?x=1'):
            with self.assertRaises(ValueError):origin(url)

    def test_repeated_timeout_backoff_and_stop(self):
        from unittest.mock import patch
        class Client:
            def request(self,*args):raise TimeoutError('offline')
        class Stop:
            def __init__(self):self.waits=[];self.stopped=False
            def is_set(self):return self.stopped
            def wait(self,delay):
                self.waits.append(delay)
                if len(self.waits)==4:self.stopped=True
                return self.stopped
        stop=Stop();discovery=Discovery(Client(),['https://localhost'],1,4)
        with patch('level1_core.discovery.random.uniform',return_value=1):discovery.run(stop)
        self.assertEqual(stop.waits,[1,2,4,4])
        self.assertEqual(discovery.status()['errors'][0]['kind'],'TimeoutError')
    def test_cached_records_expire(self):
        from unittest.mock import patch
        class Client:
            def request(self,*args):return dict(instance='node',mode='running')
        discovery=Discovery(Client(),['https://localhost'],1,2)
        discovery.probe_once()
        with patch('level1_core.discovery.time.monotonic',return_value=discovery.report['checked_at']+3):
            self.assertEqual(discovery.status()['state'],'stale')
            self.assertEqual(discovery.status()['available'],[])
