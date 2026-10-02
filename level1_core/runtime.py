"""Transactional, bounded volatile core. No algorithm learning/exchange API."""
import hashlib
import threading
import time
import uuid
from .contracts import API_VERSION, check, dumps, loads, validate
from .mock import REGISTRY

MAX_STATE = 65536

class Core:
    def __init__(self, registry=None):
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
                waiting_for='handoff' if self.mode=='paused' else ('algorithm attachment' if not self.algorithms else 'external data'),
                uptime_s=round(time.monotonic()-self.started,3),revision=self.revision,
                metrics=dict(self.metrics),algorithms={k:v.version for k,v in self.algorithms.items()},raw_records_retained=0))
    def writable(self):
        if self.mode != 'running':
            raise ValueError('Source paused')
    def attach(self, name):
        with self.lock:
            self.writable()
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
            snapshot=check('snapshot',dict(api_version=API_VERSION,state_version=1,
                handoff_id=str(uuid.uuid4()),source_instance=self.instance,destination_instance=destination,
                revision=self.revision,metrics=dict(self.metrics),
                algorithms={k:dict(version=v.version,state=v.state()) for k,v in self.algorithms.items()}))
            encoded=dumps(snapshot)
            if len(encoded)>MAX_STATE:
                raise ValueError('Snapshot too large')
            self.snapshot=loads(encoded)
            self.mode='paused'
            return loads(encoded)
    def restore(self, snapshot):
        with self.lock:
            check('snapshot',snapshot)
            encoded=dumps(snapshot)
            if len(encoded)>MAX_STATE:
                raise ValueError('Snapshot too large')
            digest=hashlib.sha256(encoded).hexdigest()
            if self.restored_digest==digest:
                return self.status()  # same destination, safe retry
            if snapshot['destination_instance']!=self.instance:
                raise ValueError('Snapshot addressed to another instance')
            if self.algorithms or self.revision or self.restored_digest or self.mode!='running' or self.metrics['failed'] or self.metrics['completed']:
                raise ValueError('Destination must be fresh')
            restored={}
            if len(snapshot['algorithms'])>16:
                raise ValueError('Too many algorithms')
            for name,item in snapshot['algorithms'].items():
                if name not in self.registry or self.registry[name].version!=item['version']:
                    raise ValueError('Algorithm version mismatch')
                restored[name]=self.registry[name](item['state'])
            self.algorithms=restored
            self.revision=snapshot['revision']
            self.metrics=dict(snapshot['metrics'])
            self.restored_digest=digest
            return self.status()
