import hashlib
import ssl
import tempfile
import threading
import unittest
import urllib.request
from examples.make_test_certs import generate
from level1_core.contracts import API_VERSION
from level1_core.runtime import Core
from level1_core.transfers import Transfers
from level1_core.server import Server,server_context
from level1_core.transport import Client
from level1_core.discovery import Discovery
from level1_core.handoff import transfer

class MTLSIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.path=generate(cls.temp.name)
        cls.ca=str(cls.path/'ca.crt')
        def pin(name):
            return hashlib.sha256(ssl.PEM_cert_to_DER_cert((cls.path/(name+'.crt')).read_text())).hexdigest()
        cls.server_pin=pin('server');cls.client_pin=pin('client')
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def setUp(self):
        tls=dict(ca=self.ca,cert=str(self.path/'server.crt'),key=str(self.path/'server.key'))
        self.servers=[Server(('127.0.0.1',0),Core(),server_context(tls),[self.client_pin]) for _ in range(2)]
        self.threads=[threading.Thread(target=s.serve_forever) for s in self.servers]
        for thread in self.threads:thread.start()
        self.urls=[f'https://localhost:{s.server_port}' for s in self.servers]
        self.peers={url:[self.server_pin] for url in self.urls}
        self.client=self.new_client()
    def new_client(self,name='client',peers=None):
        return Client(self.ca,str(self.path/(name+'.crt')),str(self.path/(name+'.key')),self.peers if peers is None else peers)
    def tearDown(self):
        for server in self.servers:server.shutdown();server.server_close()
        for thread in self.threads:thread.join()
    def test_mock_discovery_and_snapshot_mtls(self):
        self.assertEqual(len(Discovery(self.client,self.urls).probe_once()),2)
        self.client.request(self.urls[0],'/v1/algorithms/attach',dict(api_version=API_VERSION,name='mock'))
        result=self.client.request(self.urls[0],'/v1/tasks',dict(api_version=API_VERSION,algorithm='mock',data='secret'))
        self.assertTrue(result['result']['accepted'])
        transfer(self.client,*self.urls)
        transfer(self.client,*self.urls)
        self.assertEqual(self.client.request(self.urls[0],'/v1/status')['mode'],'paused')
        self.assertTrue(self.client.request(self.urls[1],'/v1/tasks',dict(api_version=API_VERSION,algorithm='mock',data=2))['result']['accepted'])
    def test_no_certificate_rejected(self):
        ctx=ssl.create_default_context(cafile=self.ca)
        with self.assertRaises(OSError):urllib.request.urlopen(self.urls[0]+'/v1/status',context=ctx,timeout=3)
    def test_ca_signed_but_not_allowlisted_client_rejected(self):
        with self.assertRaises((OSError,ValueError)):self.new_client('other').request(self.urls[0],'/v1/status')
    def test_bad_server_pin_rejected_before_request(self):
        client=self.new_client(peers={self.urls[0]:['0'*64]})
        with self.assertRaises(ValueError):client.request(self.urls[0],'/v1/algorithms/attach',dict(api_version=API_VERSION,name='mock'))
        self.assertEqual(self.servers[0].core.algorithms,{})
    def test_origin_and_hostname_rejected(self):
        with self.assertRaises(ValueError):self.client.request('https://localhost:1','/v1/status')
        ip=f'https://127.0.0.1:{self.servers[0].server_port}'
        client=self.new_client(peers={ip:[self.server_pin]})
        with self.assertRaises(ssl.SSLCertVerificationError):client.request(ip,'/v1/status')
    def test_incompatible_request_over_network(self):
        with self.assertRaises(ValueError):self.client.request(self.urls[0],'/v1/algorithms/attach',dict(api_version='2.0',name='mock'))
        self.assertEqual(self.servers[0].core.algorithms,{})
    def test_unknown_ca(self):
        client=self.new_client()
        client.context=ssl.create_default_context()
        client.context.load_cert_chain(str(self.path/'client.crt'),str(self.path/'client.key'))
        with self.assertRaises(ssl.SSLCertVerificationError):client.request(self.urls[0],'/v1/status')

    def test_numeric_evaluation_on_mtls_core(self):
        try:
            from level2_algorithms.numeric import REGISTRY
        except ImportError:
            self.skipTest('Set PYTHONPATH to genesis-level-2')
        for server in self.servers:
            server.core=Core(REGISTRY)
            server.transfers=Transfers(server.core)
        for name in ('numeric','evaluator'):
            self.client.request(self.urls[0],'/v1/algorithms/attach',dict(api_version='1.0',name=name))
        for x in (10,20,30):
            self.client.request(self.urls[0],'/v1/tasks',dict(api_version='1.0',algorithm='numeric',data=x))
        transfer(self.client,*self.urls)
        result=self.client.request(self.urls[1],'/v1/tasks',dict(api_version='1.0',algorithm='numeric',data=40))
        self.assertEqual(result['result']['mean'],25)
        for target in (1,3):
            quality=self.client.request(self.urls[1],'/v1/tasks',dict(api_version='1.0',algorithm='evaluator',data=dict(prediction=2,target=target)))
        self.assertEqual(quality['result']['mae'],1)
        self.assertEqual(quality['result']['rmse'],1)
