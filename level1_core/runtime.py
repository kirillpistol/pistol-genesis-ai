"""Transactional, bounded volatile core. No algorithm learning/exchange API."""
import hashlib
import threading
import time
import uuid
from .contracts import API_VERSION, check, dumps, loads, validate
from .mock import REGISTRY
from .bindings import digest,identifier

MAX_STATE = 65536

class Core:
    def __init__(self, registry=None, binding_policy=None, package_specs=None, worker_id=None):
        self.binding_policy=binding_policy
        self.package_specs=dict(package_specs or {})
        self.worker_id=identifier(worker_id) if binding_policy is not None else worker_id
        self.binding=None
        self.registry = dict(REGISTRY if registry is None else registry)
        self.lock = threading.RLock()
        self.instance = str(uuid.uuid4())
        self.started = time.monotonic()
        self.mode = 'running'
        self.algorithms = {}
        self.revision = 0
        self.metrics = dict(completed=0, failed=0, processing_ms=0.0)
        self.snapshot = None
        self.restored_digest = None
    def status(self):
        with self.lock:
            return check('status',dict(api_version=API_VERSION, instance=self.instance, mode=self.mode,
                waiting_for='handoff' if self.mode=='paused' else (('binding selection' if self.binding_policy is not None else 'algorithm attachment') if not self.algorithms else 'external data'),
                uptime_s=round(time.monotonic()-self.started,3),revision=self.revision,
                metrics=dict(self.metrics),algorithms={k:v.version for k,v in self.algorithms.items()},raw_records_retained=0))
    def writable(self):
        if self.mode != 'running':
            raise ValueError('Source paused')
    def attach(self, name):
        with self.lock:
            self.writable()
            if self.binding_policy is not None:raise ValueError('Strict mode requires binding selection')
            if name not in self.registry or name in self.algorithms or len(self.algorithms)>=16:
                raise ValueError('Unknown, duplicate or excessive algorithm')
            candidate = self.registry[name]()
            validate(candidate.state(),{'type':'object'})
            if len(dumps(candidate.state()))>MAX_STATE:
                raise ValueError('State too large')
            self.algorithms[name] = candidate
            self.revision += 1
            return self.status()
    def process(self, name, data):
        if self.binding_policy is not None:raise ValueError('Legacy tasks forbidden in strict mode')
        return self._process(name,data)
    def _process(self,name,data):
        with self.lock:
            self.writable()
            start=time.perf_counter()
            try:
                if name not in self.algorithms:
                    raise ValueError('Attach algorithm first')
                # Candidate state prevents mutations on failed processing from reaching live state.
                candidate=self.registry[name](loads(dumps(self.algorithms[name].state())))
                result=candidate.process(data)
                validate(candidate.state(),{'type':'object'})
                encoded=dumps(candidate.state())
                if len(encoded)>MAX_STATE or len(dumps(result))>MAX_STATE:
                    raise ValueError('Algorithm state/result too large')
                response=check('task_response',dict(api_version=API_VERSION,algorithm=name,result=result,revision=self.revision+1))
                if len(dumps(response))>MAX_STATE:
                    raise ValueError('Response envelope too large')
                self.algorithms[name]=candidate
                self.revision+=1
                self.metrics['completed']+=1
                return response
            except Exception:
                self.metrics['failed']+=1
                raise
            finally:
                self.metrics['processing_ms']+=(time.perf_counter()-start)*1000
    def export(self, destination):
        with self.lock:
            if destination==self.instance:
                raise ValueError('Cannot handoff to self')
            if self.snapshot:
                if self.snapshot['destination_instance'] != destination:
                    raise ValueError('Handoff already bound to another destination')
                return loads(dumps(self.snapshot))
            self.writable()
            snapshot=dict(api_version=API_VERSION,state_version=1,
                handoff_id=str(uuid.uuid4()),source_instance=self.instance,destination_instance=destination,
                revision=self.revision,metrics=dict(self.metrics),
                algorithms={k:dict(version=v.version,state=v.state()) for k,v in self.algorithms.items()})
            if self.binding_policy is not None:
                if self.binding is None:raise ValueError('No selected binding to transfer')
                snapshot['state_version']=2
                snapshot['binding']=loads(dumps(self.binding))
            self.validate_snapshot(snapshot)
            encoded=dumps(snapshot)
            if len(encoded)>MAX_STATE:
                raise ValueError('Snapshot too large')
            self.snapshot=loads(encoded)
            self.mode='paused'
            return loads(encoded)
    def restore(self, snapshot):
        with self.lock:
            self.validate_snapshot(snapshot)
            encoded=dumps(snapshot)
            if len(encoded)>MAX_STATE:
                raise ValueError('Snapshot too large')
            snapshot_digest=hashlib.sha256(encoded).hexdigest()
            if self.restored_digest==snapshot_digest:
                return self.status()  # same destination, safe retry
            if snapshot['destination_instance']!=self.instance:
                raise ValueError('Snapshot addressed to another instance')
            if self.algorithms or self.revision or self.restored_digest or self.mode!='running' or self.metrics['failed'] or self.metrics['completed']:
                raise ValueError('Destination must be fresh')
            restored_binding=None
            if self.binding_policy is not None:
                incoming=snapshot['binding']
                passport=self.binding_policy.passport(incoming['passport']['binding_id'],self.registry,self.package_specs,self.worker_id)
                if passport!=incoming['passport'] or digest(passport)!=incoming['passport_sha256']:
                    raise ValueError('Local approved passport differs from snapshot')
                if not 0<=incoming['next_record']<=len(self.binding_policy.positions[passport['binding_id']]):
                    raise ValueError('Invalid bound cursor')
                if set(snapshot['algorithms'])!={passport['level2_id']}:
                    raise ValueError('Snapshot contains another algorithm')
                restored_binding=loads(dumps(incoming))
            restored={}
            if len(snapshot['algorithms'])>16:
                raise ValueError('Too many algorithms')
            for name,item in snapshot['algorithms'].items():
                if name not in self.registry or self.registry[name].version!=item['version']:
                    raise ValueError('Algorithm version mismatch')
                restored[name]=self.registry[name](item['state'])
            self.algorithms=restored
            self.binding=restored_binding
            self.revision=snapshot['revision']
            self.metrics=dict(snapshot['metrics'])
            self.restored_digest=snapshot_digest
            return self.status()

    def validate_snapshot(self,snapshot):
        version=snapshot.get('state_version') if isinstance(snapshot,dict) else None
        if self.binding_policy is not None:
            if version!=2:raise ValueError('Strict receiver requires bound snapshot v2')
            return check('bound_snapshot',snapshot)
        if version!=1:raise ValueError('Bound snapshot requires approved local policy')
        return check('snapshot',snapshot)
    def fresh_candidate(self):
        return Core(self.registry,self.binding_policy,self.package_specs,self.worker_id)
    def select_binding(self,binding_id):
        with self.lock:
            self.writable()
            if self.binding_policy is None:raise ValueError('Strict policy not configured')
            passport=self.binding_policy.passport(binding_id,self.registry,self.package_specs,self.worker_id)
            if self.binding:
                if self.binding['passport']!=passport:raise ValueError('Finish and close current binding first')
                return self.binding_status()
            if self.algorithms:raise ValueError('Cannot adopt unbound algorithm state')
            name=passport['level2_id'];candidate=self.registry[name]()
            validate(candidate.state(),{'type':'object'})
            if len(dumps(candidate.state()))>MAX_STATE:raise ValueError('State too large')
            self.binding=dict(passport=passport,passport_sha256=digest(passport),next_record=0)
            self.algorithms={name:candidate};self.revision+=1
            return self.binding_status()
    def binding_status(self,pin=None):
        with self.lock:
            if self.binding is None:raise ValueError('No selected binding')
            if pin is not None:self.binding_policy.authorize(pin,self.binding['passport']['binding_id'])
            return check('binding_status',dict(api_version=API_VERSION,worker_id=self.worker_id,binding=loads(dumps(self.binding))))
    def binding_catalog(self,pin=None):
        with self.lock:
            if self.binding_policy is None:raise ValueError('Strict policy not configured')
            items=[]
            for binding_id in self.binding_policy.passports:
                if pin is not None and binding_id not in self.binding_policy.grants.get(pin,()):continue
                try:passport=self.binding_policy.passport(binding_id,self.registry,self.package_specs,self.worker_id)
                except ValueError:continue
                items.append(dict(binding_id=binding_id,passport_sha256=digest(passport),active=self.mode=='running' and self.binding is not None and self.binding['passport']['binding_id']==binding_id))
            return check('binding_catalog',dict(api_version=API_VERSION,worker_id=self.worker_id,items=items))
    def process_bound(self,message,pin):
        with self.lock:
            self.writable();check('binding_task',message)
            if self.binding_policy is None or self.binding is None:raise ValueError('No active strict binding')
            passport=self.binding['passport']
            self.binding_policy.verify_record(passport,message,self.binding['next_record'],pin)
            response=self._process(passport['level2_id'],message['data'])
            self.binding['next_record']+=1
            return response
    def close_binding(self,binding_id):
        with self.lock:
            self.writable()
            if self.binding is None or self.binding['passport']['binding_id']!=binding_id:raise ValueError('Wrong active binding')
            if self.binding['next_record']!=len(self.binding_policy.positions[binding_id]):raise ValueError('Task not complete')
            self.algorithms={};self.binding=None;self.revision+=1
            return self.status()
