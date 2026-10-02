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
