"""Strict level 2/3 pair; parts on two mTLS servers, binding survives worker transfer."""
import copy
import hashlib
import os
import ssl
import tempfile
import threading
import unittest
from pathlib import Path
from examples.make_test_certs import generate
from level1_core.bindings import BindingPolicy,digest
from level1_core.contracts import dumps
from level1_core.discovery import Discovery
from level1_core.handoff import transfer,rollback
from level1_core.runtime import Core
from level1_core.server import Server,server_context
from level1_core.transport import Client,PeerError
try:
    from level2_algorithms.bindings_demo import REGISTRY,PACKAGE_SPECS
    from level3_data.bound import BoundAdapter,PartClient
    from level3_data.serve_parts import PartServer
except ImportError:
    if os.environ.get('GENESIS_REQUIRE_E2E')=='1':raise
    REGISTRY=None

@unittest.skipIf(REGISTRY is None,'Requires independent level 2 and 3 checkouts')
class BoundE2ETests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.root=generate(cls.temp.name)
        cls.pin=staticmethod(lambda name:hashlib.sha256(ssl.PEM_cert_to_DER_cert((cls.root/(name+'.crt')).read_text())).hexdigest())
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def setUp(self):
        self.files_temp=tempfile.TemporaryDirectory();root=Path(self.files_temp.name)
        tls=dict(ca=str(self.root/'ca.crt'),cert=str(self.root/'server.crt'),key=str(self.root/'server.key'))
        self.sources=[];self.manifests={};passports=[]
        for sector in ('a','b'):
            binding='sector-'+sector
            parts=[]
            for i,values in enumerate(((10,20),(30,40))):
                raw=b''.join(dumps(value)+b'\n' for value in values)
                path=root/(binding+'-'+str(i)+'.ndjson');path.write_bytes(raw)
                part_id='part-'+str(i)
                server=PartServer(('127.0.0.1',0),server_context(tls),[self.pin('data' if sector=='a' else 'other')],{'/v1/data/parts/'+part_id:path})
                self.sources.append(server)
                parts.append(dict(part_id=part_id,source_id=binding+'-source-'+str(i),origin=f'https://localhost:{server.server_port}',size=len(raw),checksum=hashlib.sha256(raw).hexdigest(),record_hashes=[digest(value) for value in values]))
            spec=PACKAGE_SPECS[binding+'.numeric']
            manifest=dict(binding_id=binding,manifest_id=binding+'-data',data_version='42',input_contract=spec['input_contract'],format='ndjson/1',parts=parts)
            self.manifests[binding]=manifest
            passports.append(dict(binding_id=binding,level2_id=binding+'.numeric',**spec,level3_manifest_id=manifest['manifest_id'],data_version='42',manifest_sha256=digest(manifest),allowed_workers=['worker-a','worker-b']))
        document=dict(bindings=passports,data_grants={self.pin('data'):['sector-a'],self.pin('other'):['sector-b']})
        self.policy=BindingPolicy(document,self.manifests)
        roles={self.pin('client'):['control'],self.pin('data'):['data'],self.pin('other'):['data']}
        self.nodes=[Server(('127.0.0.1',0),Core(REGISTRY,self.policy,PACKAGE_SPECS,worker),server_context(tls),client_roles=roles) for worker in ('worker-a','worker-b')]
        self.servers=self.sources+self.nodes
        self.threads=[threading.Thread(target=server.serve_forever) for server in self.servers]
        for thread in self.threads:thread.start()
        self.urls=[f'https://localhost:{server.server_port}' for server in self.nodes]
        peers={url:[self.pin('server')] for url in self.urls}
        def client(name):return Client(str(self.root/'ca.crt'),str(self.root/(name+'.crt')),str(self.root/(name+'.key')),peers)
        self.control,self.data,self.other=client('client'),client('data'),client('other')
        source_peers={part['origin']:[self.pin('server')] for manifest in self.manifests.values() for part in manifest['parts']}
        self.parts=PartClient(str(self.root/'ca.crt'),str(self.root/'data.crt'),str(self.root/'data.key'),source_peers)
    def tearDown(self):
        for server in self.servers:server.shutdown();server.server_close()
        for thread in self.threads:thread.join()
        self.files_temp.cleanup()
    def select(self,binding='sector-a'):
        return self.control.request(self.urls[0],'/v1/bindings/select',dict(api_version='1.0',binding_id=binding))
    def adapter(self,index=0,binding='sector-a',client=None):
        return BoundAdapter(client or self.data,self.urls[index],self.policy.passports[binding],self.manifests[binding],self.parts)
    def test_distributed_collection_exact_pair_and_transfer(self):
        passport=self.policy.passports['sector-a']
        choice=Discovery(self.control,self.urls).choose_binding('sector-a',digest(passport),active=False)
        self.assertEqual(choice['worker_id'],'worker-a')
        self.select()
        stream=self.adapter().deliver(correlation_id='bound-e2e')
        next(stream);next(stream);stream.close() # first part processed; second remains on another source server
        self.assertEqual(self.nodes[0].core.binding['next_record'],2)
        self.assertEqual(self.nodes[0].core.algorithms['sector-a.numeric'].state()['n'],2)
        transfer(self.control,*self.urls,backoff=0)
        self.assertEqual(self.nodes[1].core.binding['passport'],passport)
        self.assertEqual(self.nodes[1].core.binding['next_record'],2)
        choice=Discovery(self.data,self.urls).choose_binding('sector-a',digest(passport))
        self.assertEqual(choice['worker_id'],'worker-b')
        results=list(self.adapter(1).deliver(correlation_id='bound-e2e'))
        self.assertEqual(len(results),2)
        self.assertEqual(results[-1]['result']['count'],4)
        self.assertEqual(results[-1]['result']['mean'],25)
        self.assertEqual(list(self.adapter(1).deliver()),[]) # confirmed cursor: no repeated processing
        self.assertEqual(self.nodes[1].core.metrics['completed'],4)
    def test_wrong_sector_identity_and_legacy_bypass(self):
        self.select()
        self.assertEqual(self.other.request(self.urls[0],'/v1/bindings/catalog')['items'][0]['binding_id'],'sector-b')
        with self.assertRaises(PeerError) as error:self.other.request(self.urls[0],'/v1/bindings/status')
        self.assertEqual(error.exception.status,403)
        for path,body,client in [
            ('/v1/algorithms/attach',dict(api_version='1.0',name='sector-b.numeric'),self.control),
            ('/v1/tasks',dict(api_version='1.0',algorithm='sector-a.numeric',data=10),self.data),
            ('/v1/snapshot',dict(api_version='1.0',destination_instance='wrong'),self.control)]:
            with self.assertRaises(PeerError):client.request(self.urls[0],path,body)
        self.assertEqual(self.nodes[0].core.mode,'running')
        adapter=self.adapter();part=self.manifests['sector-a']['parts'][0]
        message=adapter.message(part,0,10)
        with self.assertRaises(PeerError) as error:self.other.request(self.urls[0],'/v1/bindings/tasks',message)
        self.assertEqual(error.exception.status,403)
        message['data']=999
        with self.assertRaises(PeerError):self.data.request(self.urls[0],'/v1/bindings/tasks',message)
        self.assertEqual(self.nodes[0].core.binding['next_record'],0)
        with self.assertRaises(ValueError):Discovery(self.data,self.urls).choose_binding('sector-b',digest(self.policy.passports['sector-b']))
    def test_receiver_wrong_package_blocks_finish_and_allows_rollback(self):
        self.select();next(self.adapter().deliver())
        self.nodes[1].core.package_specs=copy.deepcopy(PACKAGE_SPECS)
        self.nodes[1].core.package_specs['sector-a.numeric']['package_sha256']='0'*64
        with self.assertRaises(PeerError):transfer(self.control,*self.urls,backoff=0)
        identifier=self.nodes[0].transfers.outgoing['meta']['transfer_id']
        self.assertFalse(self.nodes[0].transfers.outgoing['released'])
        self.assertEqual(self.nodes[1].core.algorithms,{})
        rollback(self.control,*self.urls,identifier)
        self.assertEqual(self.nodes[0].core.mode,'running')
        self.assertEqual(self.nodes[0].core.binding['next_record'],1)
    def test_modified_source_rejected_before_algorithm(self):
        self.select()
        path=self.sources[0].files['/v1/data/parts/part-0']
        path.write_bytes(b'99\n20\n')
        with self.assertRaises(ValueError):list(self.adapter().deliver())
        self.assertEqual(self.nodes[0].core.algorithms['sector-a.numeric'].state()['n'],0)
        self.assertEqual(self.nodes[0].core.binding['next_record'],0)

    def test_sector_a_identity_cannot_fetch_sector_b_parts(self):
        import http.client
        part=self.manifests['sector-b']['parts'][0]
        with self.assertRaises((OSError,http.client.HTTPException,ValueError)):self.parts.fetch(part)
