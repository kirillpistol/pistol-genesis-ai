"""One real external HTTP stream -> Adapter -> mTLS -> Discovery -> transfer -> quality."""
import base64
import hashlib
import json
import os
import ssl
import tempfile
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from examples.make_test_certs import generate
from level1_core.runtime import Core
from level1_core.server import Server,server_context
from level1_core.transport import Client,PeerError
from level1_core.discovery import Discovery
from level1_core.handoff import transfer,rollback
try:
    from level2_algorithms.numeric import REGISTRY
    from level3_data.adapter import Adapter
except ImportError:
    if os.environ.get('GENESIS_REQUIRE_E2E')=='1':raise
    REGISTRY=None

class ExternalSource(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        self.send_response(200);self.send_header('Content-Type','application/x-ndjson');self.end_headers()
        for value in (10,20,30,40):
            self.wfile.write((json.dumps(value)+'\n').encode());self.wfile.flush()

@unittest.skipIf(REGISTRY is None,'Requires separate level 2 and level 3 checkouts on PYTHONPATH')
class E2ETests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.path=generate(cls.temp.name)
        cls.pin=staticmethod(lambda name:hashlib.sha256(ssl.PEM_cert_to_DER_cert((cls.path/(name+'.crt')).read_text())).hexdigest())
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def setUp(self):
        path=self.path
        tls=dict(ca=str(path/'ca.crt'),cert=str(path/'server.crt'),key=str(path/'server.key'))
        roles={self.pin('client'):['control'],self.pin('data'):['data']}
        self.nodes=[Server(('127.0.0.1',0),Core(REGISTRY),server_context(tls),client_roles=roles) for _ in range(2)]
        self.source=ThreadingHTTPServer(('127.0.0.1',0),ExternalSource)
        self.servers=[*self.nodes,self.source]
        self.threads=[threading.Thread(target=s.serve_forever) for s in self.servers]
        for thread in self.threads:thread.start()
        self.urls=[f'https://localhost:{node.server_port}' for node in self.nodes]
        peers={url:[self.pin('server')] for url in self.urls}
        self.control=Client(str(path/'ca.crt'),str(path/'client.crt'),str(path/'client.key'),peers)
        self.data=Client(str(path/'ca.crt'),str(path/'data.crt'),str(path/'data.key'),peers)
    def tearDown(self):
        for server in self.servers:server.shutdown();server.server_close()
        for thread in self.threads:thread.join()
    def attach(self):
        for name in ('numeric','evaluator'):
            self.control.request(self.urls[0],'/v1/algorithms/attach',dict(api_version='1.0',name=name))
    def test_whole_chain_with_resume_lost_ack_and_quality(self):
        self.attach()
        discovery=Discovery(self.data,self.urls)
        chosen=discovery.choose(algorithm='numeric')
        self.assertEqual(chosen['endpoint'],self.urls[0])
        adapter=Adapter(self.data,chosen['endpoint'],'numeric')
        external=f'http://127.0.0.1:{self.source.server_port}/numbers'
        with urllib.request.urlopen(external,timeout=3) as response:
            records=(json.loads(line) for line in response)
            for _ in range(3):adapter.send(next(records),correlation_id='full-e2e')
            target=self.control.request(self.urls[1],'/v1/status')
            meta=self.control.request(self.urls[0],'/v1/transfers/prepare',dict(api_version='1.0',destination_instance=target['instance']))
            identifier=meta['transfer_id']
            self.control.request(self.urls[1],'/v1/transfers/begin',meta)
            chunk=self.control.request(self.urls[0],'/v1/transfers/download',dict(api_version='1.0',transfer_id=identifier,offset=0))
            partial=base64.b64decode(chunk['chunk_base64'])[:50]
            self.control.request(self.urls[1],'/v1/transfers/chunk',dict(api_version='1.0',transfer_id=identifier,offset=0,chunk_base64=base64.b64encode(partial).decode()))
            class LostAck:
                def __init__(inner):inner.lost=False
                def request(inner,url,path,body=None,**kwargs):
                    result=self.control.request(url,path,body,**kwargs)
                    if path=='/v1/transfers/chunk' and not inner.lost:
                        inner.lost=True;raise TimeoutError('Simulated lost acknowledgement')
                    return result
            client=LostAck()
            restored=transfer(client,*self.urls,backoff=0,correlation_id='full-e2e')
            self.assertTrue(client.lost)
            self.assertEqual(restored['metrics']['completed'],3)
            self.assertEqual(self.nodes[0].core.mode,'paused')
            self.assertEqual(self.nodes[1].core.restored_digest,meta['checksum'])
            chosen=discovery.choose(algorithm='numeric')
            self.assertEqual(chosen['endpoint'],self.urls[1])
            result=Adapter(self.data,chosen['endpoint'],'numeric').send(next(records),correlation_id='full-e2e')
            self.assertEqual(result['result']['count'],4)
            self.assertEqual(result['result']['mean'],25)
        evaluator=Adapter(self.data,self.urls[1],'evaluator')
        for target in (1,3):quality=evaluator.send(dict(prediction=2,target=target),correlation_id='full-e2e')
        self.assertEqual(quality['result'],dict(count=2,mae=1.0,rmse=1.0))
        self.assertEqual(self.nodes[1].core.metrics['completed'],6)
        with self.assertRaises(ValueError):rollback(self.control,*self.urls,identifier)
    def test_roles_control_data(self):
        with self.assertRaises(PeerError) as error:self.data.request(self.urls[0],'/v1/algorithms/attach',dict(api_version='1.0',name='numeric'))
        self.assertEqual(error.exception.status,403)
        self.attach()
        with self.assertRaises(PeerError) as error:Adapter(self.control,self.urls[0],'numeric').send(1)
        self.assertEqual(error.exception.status,403)
        self.assertTrue(self.data.request(self.urls[0],'/v1/readiness')['ready'])
        self.assertTrue(self.control.request(self.urls[0],'/v1/health')['alive'])
    def test_snapshot_rollback_before_release(self):
        self.attach()
        target=self.control.request(self.urls[1],'/v1/status')
        meta=self.control.request(self.urls[0],'/v1/transfers/prepare',dict(api_version='1.0',destination_instance=target['instance']))
        self.control.request(self.urls[1],'/v1/transfers/begin',meta)
        rollback(self.control,*self.urls,meta['transfer_id'])
        self.assertTrue(self.data.request(self.urls[0],'/v1/readiness')['ready'])
        self.assertEqual(self.nodes[1].core.algorithms,{})
    def test_dead_node_after_discovery_is_not_reused(self):
        discovery=Discovery(self.data,self.urls)
        self.assertEqual(len(discovery.probe_once()),2)
        self.nodes[0].shutdown();self.nodes[0].server_close()
        chosen=discovery.choose()
        self.assertEqual(chosen['endpoint'],self.urls[1])
        self.assertEqual(len(discovery.status()['errors']),1)

    def test_interrupted_chunk_body_then_resume(self):
        import socket
        from urllib.parse import urlsplit
        self.attach()
        target=self.control.request(self.urls[1],'/v1/status')
        meta=self.control.request(self.urls[0],'/v1/transfers/prepare',dict(api_version='1.0',destination_instance=target['instance']))
        self.control.request(self.urls[1],'/v1/transfers/begin',meta)
        identifier=meta['transfer_id']
        request=dict(api_version='1.0',transfer_id=identifier)
        chunk=self.control.request(self.urls[0],'/v1/transfers/download',dict(**request,offset=0))
        body=json.dumps(dict(**request,offset=0,chunk_base64=chunk['chunk_base64'])).encode()
        address=urlsplit(self.urls[1])
        raw=socket.create_connection(('localhost',address.port),timeout=3)
        conn=self.control.context.wrap_socket(raw,server_hostname='localhost')
        headers=('POST /v1/transfers/chunk HTTP/1.0\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: '+str(len(body))+'\r\n\r\n').encode()
        conn.sendall(headers+body[:len(body)//2])
        conn.close() # truncated TLS request: no chunk can be applied
        progress=self.control.request(self.urls[1],'/v1/transfers/progress',request)
        self.assertEqual(progress['next_offset'],0)
        restored=transfer(self.control,*self.urls,backoff=0)
        self.assertEqual(restored['algorithms']['numeric'],'numeric/1')
        self.assertEqual(Adapter(self.data,self.urls[1],'numeric').send(10)['result']['count'],1)
