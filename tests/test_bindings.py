"""Strict passport unit tests with synthetic operator-authored manifests."""
import copy
import hashlib
import unittest
from level1_core.bindings import BindingPolicy,digest
from level1_core.runtime import Core

PIN='a'*64
OTHER='b'*64
class Algorithm:
    version='manual/1'
    def __init__(self,state=None):self.n=0 if state is None else state['n']
    def process(self,value):self.n+=1;return dict(count=self.n)
    def state(self):return dict(n=self.n)
REGISTRY={'manual-a':Algorithm,'manual-b':Algorithm}
SPECS={name:dict(algorithm_version='manual/1',model_version='model/1',input_contract=name+'/1',package_sha256='c'*64) for name in REGISTRY}

def fixtures():
    passports=[];manifests={}
    for name in REGISTRY:
        raw=b'10\n20\n'
        manifest=dict(binding_id=name,manifest_id=name+'-data',data_version='42',input_contract=name+'/1',format='ndjson/1',
            parts=[dict(part_id='part-1',source_id='source-1',origin='https://localhost:9443',size=len(raw),checksum=hashlib.sha256(raw).hexdigest(),record_hashes=[digest(10),digest(20)])])
        passport=dict(binding_id=name,level2_id=name,**SPECS[name],level3_manifest_id=manifest['manifest_id'],data_version='42',manifest_sha256=digest(manifest),allowed_workers=['worker-a','worker-b'])
        passports.append(passport);manifests[name]=manifest
    document=dict(bindings=passports,data_grants={PIN:['manual-a'],OTHER:['manual-b']})
    return document,manifests

def message(policy,name='manual-a',index=0,data=10):
    passport=policy.passports[name]
    return dict(api_version='1.0',binding_id=name,manifest_id=passport['level3_manifest_id'],data_version='42',manifest_sha256=passport['manifest_sha256'],input_contract=passport['input_contract'],source_id='source-1',part_id='part-1',record_index=index,data=data)

class BindingTests(unittest.TestCase):
    def setUp(self):
        document,manifests=fixtures();self.policy=BindingPolicy(document,manifests)
        self.core=Core(REGISTRY,self.policy,SPECS,'worker-a');self.core.select_binding('manual-a')
    def test_exact_binding_and_duplicate_rejection(self):
        self.assertEqual(self.core.process_bound(message(self.policy),PIN)['result']['count'],1)
        with self.assertRaises(ValueError):self.core.process_bound(message(self.policy),PIN)
        self.assertEqual(self.core.binding['next_record'],1)
    def test_legacy_bypass_and_sector_switch_rejected(self):
        with self.assertRaises(ValueError):self.core.attach('manual-b')
        with self.assertRaises(ValueError):self.core.process('manual-a',10)
        with self.assertRaises(ValueError):self.core.select_binding('manual-b')
        with self.assertRaises(ValueError):self.core.close_binding('manual-a')
    def test_identity_scope_and_spoofed_record(self):
        with self.assertRaises(PermissionError):self.core.process_bound(message(self.policy),OTHER)
        with self.assertRaises(ValueError):self.core.process_bound(message(self.policy,data=999),PIN)
        with self.assertRaises(ValueError):self.core.process_bound(message(self.policy,name='manual-b'),PIN)
        self.assertEqual(self.core.algorithms['manual-a'].n,0)
    def test_wrong_data_versions_fields_and_order(self):
        for key,value in [('data_version','43'),('manifest_sha256','0'*64),('manifest_id','wrong'),('input_contract','manual-b/1'),('source_id','wrong'),('part_id','wrong'),('record_index',1)]:
            body=message(self.policy);body[key]=value
            with self.assertRaises(ValueError):self.core.process_bound(body,PIN)
        self.assertEqual(self.core.binding['next_record'],0)
    def test_package_and_worker_rejected(self):
        for field in ('algorithm_version','model_version','input_contract','package_sha256'):
            specs=copy.deepcopy(SPECS);specs['manual-a'][field]='wrong'
            core=Core(REGISTRY,self.policy,specs,'worker-a')
            with self.assertRaises(ValueError):core.select_binding('manual-a')
            self.assertEqual(core.algorithms,{})
        with self.assertRaises(ValueError):Core(REGISTRY,self.policy,SPECS,'worker-c').select_binding('manual-a')
    def test_bound_snapshot_keeps_exact_cursor(self):
        self.core.process_bound(message(self.policy),PIN)
        target=Core(REGISTRY,self.policy,SPECS,'worker-b')
        snapshot=self.core.export(target.instance)
        self.assertEqual(snapshot['state_version'],2)
        target.restore(snapshot)
        self.assertEqual(target.binding,self.core.binding)
        self.assertEqual(target.process_bound(message(self.policy,index=1,data=20),PIN)['result']['count'],2)
        with self.assertRaises(ValueError):target.process_bound(message(self.policy,index=1,data=20),PIN)
    def test_receiver_requires_same_policy_and_no_legacy_snapshot(self):
        target=Core(REGISTRY,self.policy,SPECS,'worker-b')
        document,manifests=fixtures();document['bindings'][0]['model_version']='model/2'
        altered=BindingPolicy(document,manifests)
        other=Core(REGISTRY,altered,SPECS,'worker-b')
        snapshot=self.core.export(other.instance)
        with self.assertRaises(ValueError):other.restore(snapshot)
        self.assertEqual(other.algorithms,{})
        unbound=Core(REGISTRY);unbound.attach('manual-a')
        legacy=unbound.export(target.instance)
        with self.assertRaises(ValueError):target.restore(legacy)
    def test_complete_close_then_another_sector(self):
        self.core.process_bound(message(self.policy),PIN)
        self.core.process_bound(message(self.policy,index=1,data=20),PIN)
        self.core.close_binding('manual-a')
        self.core.select_binding('manual-b')
        self.assertEqual(self.core.process_bound(message(self.policy,'manual-b'),OTHER)['result']['count'],1)
    def test_manifest_owner_cannot_be_reassigned(self):
        document,manifests=fixtures()
        document['bindings'][1]['level3_manifest_id']=document['bindings'][0]['level3_manifest_id']
        manifests['manual-b']['manifest_id']=manifests['manual-a']['manifest_id']
        document['bindings'][1]['manifest_sha256']=digest(manifests['manual-b'])
        with self.assertRaises(ValueError):BindingPolicy(document,manifests)
