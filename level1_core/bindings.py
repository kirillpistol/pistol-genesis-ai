"""Local operator-approved passports and per-certificate dataset scopes."""
import hashlib
import re
from pathlib import Path
from .contracts import check,dumps,loads

IDENTIFIER=re.compile(r'^[A-Za-z0-9._-]{1,128}$')

def digest(value):return hashlib.sha256(dumps(value)).hexdigest()

def sha(value):
    if not isinstance(value,str) or len(value)!=64 or any(c not in '0123456789abcdef' for c in value):
        raise ValueError('Invalid SHA256')
    return value

def identifier(value):
    if not isinstance(value,str) or not IDENTIFIER.fullmatch(value):raise ValueError('Invalid identifier')
    return value

class BindingPolicy:
    def __init__(self,document,manifests):
        if type(document) is not dict or set(document)!={'bindings','data_grants'}:
            raise ValueError('Invalid local binding policy')
        if type(document['bindings']) is not list or not 1<=len(document['bindings'])<=64:
            raise ValueError('Expected 1..64 passports')
        self.passports={};self.manifests={};self.positions={};self.grants={}
        owners={}
        for passport in document['bindings']:
            check('binding_passport',passport)
            binding=identifier(passport['binding_id'])
            if binding in self.passports:raise ValueError('Duplicate binding')
            for name in ('level2_id','level3_manifest_id'):identifier(passport[name])
            for name in ('package_sha256','manifest_sha256'):sha(passport[name])
            if not passport['allowed_workers'] or len(set(passport['allowed_workers']))!=len(passport['allowed_workers']):
                raise ValueError('Expected unique allowed workers')
            for worker in passport['allowed_workers']:identifier(worker)
            manifest=manifests[binding]
            check('data_manifest',manifest)
            if digest(manifest)!=passport['manifest_sha256']:raise ValueError('Manifest checksum mismatch')
            expected={'binding_id':binding,'manifest_id':passport['level3_manifest_id'],
                      'data_version':passport['data_version'],'input_contract':passport['input_contract']}
            if any(manifest[k]!=v for k,v in expected.items()):raise ValueError('Manifest/passport mismatch')
            owner=(manifest['manifest_id'],manifest['data_version'])
            if owner in owners and owners[owner]!=passport['level2_id']:raise ValueError('Manifest has another level 2 owner')
            owners[owner]=passport['level2_id']
            positions=[];parts=set()
            from .transport import origin
            for part in manifest['parts']:
                origin(part['origin'])
                identifier(part['part_id']);identifier(part['source_id']);sha(part['checksum'])
                if part['part_id'] in parts:raise ValueError('Duplicate part')
                parts.add(part['part_id'])
                if not part['record_hashes']:raise ValueError('Empty part')
                for index,record_hash in enumerate(part['record_hashes']):
                    sha(record_hash)
                    positions.append(dict(source_id=part['source_id'],part_id=part['part_id'],record_index=index,record_hash=record_hash))
            if not 1<=len(positions)<=4096:raise ValueError('Dataset metadata limit')
            self.passports[binding]=loads(dumps(passport));self.manifests[binding]=loads(dumps(manifest));self.positions[binding]=positions
        if type(document['data_grants']) is not dict:raise ValueError('Invalid data grants')
        for pin,bindings in document['data_grants'].items():
            sha(pin)
            if type(bindings) is not list or not bindings or len(set(bindings))!=len(bindings) or any(b not in self.passports for b in bindings):
                raise ValueError('Invalid binding scope')
            self.grants[pin]=frozenset(bindings)
    @classmethod
    def load(cls,path):
        path=Path(path).resolve()
        if path.stat().st_size>1048576:raise ValueError('Policy too large')
        config=loads(path.read_bytes())
        if set(config)!={'bindings','data_grants','manifests'}:raise ValueError('Invalid policy file')
        manifests={}
        for binding,relative in config.pop('manifests').items():
            target=(path.parent/relative).resolve()
            if not target.is_relative_to(path.parent) or target.stat().st_size>1048576:raise ValueError('Invalid manifest path/size')
            manifests[binding]=loads(target.read_bytes())
        return cls(config,manifests)
    def passport(self,binding,registry,specs,worker):
        if binding not in self.passports:raise ValueError('Unknown binding; no fallback')
        passport=self.passports[binding];name=passport['level2_id']
        if worker not in passport['allowed_workers']:raise ValueError('Worker forbidden')
        if name not in registry or name not in specs:raise ValueError('Required level 2 package absent')
        if registry[name].version!=passport['algorithm_version']:raise ValueError('Algorithm version mismatch')
        for field in ('algorithm_version','model_version','input_contract','package_sha256'):
            if specs[name].get(field)!=passport[field]:raise ValueError('Package passport mismatch')
        return loads(dumps(passport))
    def authorize(self,pin,binding):
        if binding not in self.grants.get(pin,()):raise PermissionError('Data identity has no scope for this binding')
    def verify_record(self,passport,message,position,pin):
        binding=passport['binding_id'];self.authorize(pin,binding)
        for field in ('binding_id','data_version','manifest_sha256','input_contract'):
            if message[field]!=passport[field]:raise ValueError('Data belongs to another binding/version')
        if message['manifest_id']!=passport['level3_manifest_id']:raise ValueError('Wrong manifest')
        positions=self.positions[binding]
        if position>=len(positions):raise ValueError('Dataset already consumed')
        expected=positions[position]
        for field in ('source_id','part_id','record_index'):
            if message[field]!=expected[field]:raise ValueError('Duplicate or out of order record')
        if digest(message['data'])!=expected['record_hash']:raise ValueError('Record not committed by approved manifest')
