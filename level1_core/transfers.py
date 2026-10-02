"""Bounded volatile staged transfer. Rollback only before release decision."""
import base64
import hashlib
import secrets
import time
from .contracts import API_VERSION, check, dumps, loads
from .runtime import Core, MAX_STATE

CHUNK_SIZE=4096

class Transfers:
    def __init__(self,core):
        self.core=core
        self.outgoing=None
        self.incoming=None
        self.terminal={}  # bounded replay tombstones; never evict silently
    def _new(self,identifier):
        if identifier in self.terminal:
            raise ValueError('Transfer already terminated')
        if len(self.terminal)>=1024:
            raise ValueError('Replay guard full; restart requires new instances')
    def prepare(self,destination):
        with self.core.lock:
            if self.incoming:
                raise ValueError('Receiving transfer')
            if self.outgoing:
                if self.outgoing['meta']['destination_instance']!=destination:
                    raise ValueError('Already transferring elsewhere')
                return dict(self.outgoing['meta'])
            if self.core.snapshot:
                raise ValueError('Legacy snapshot cannot be rolled back by this protocol')
            if len(self.terminal)>=1024:raise ValueError('Replay guard full')
            snapshot=self.core.export(destination)
            identifier=snapshot['handoff_id']
            self._new(identifier)
            raw=dumps(snapshot)
            token=secrets.token_hex(32)
            meta=dict(api_version=API_VERSION,transfer_id=identifier,
                      source_instance=self.core.instance,destination_instance=destination,
                      checksum=hashlib.sha256(raw).hexdigest(),size=len(raw),
                      token_hash=hashlib.sha256(token.encode()).hexdigest())
            self.outgoing=dict(meta=meta,raw=raw,token=token,released=False)
            return dict(meta)
    def download(self,identifier,offset):
        with self.core.lock:
            entry=self._out(identifier)
            if not 0<=offset<=len(entry['raw']):raise ValueError('Invalid offset')
            raw=entry['raw'][offset:offset+CHUNK_SIZE]
            return dict(api_version=API_VERSION,transfer_id=identifier,offset=offset,
                        chunk_base64=base64.b64encode(raw).decode(),next_offset=offset+len(raw))
    def begin(self,meta):
        with self.core.lock:
            identifier=meta['transfer_id']
            if self.incoming:
                if self.incoming['meta']!=meta:raise ValueError('Another transfer is pending')
                return self.progress(identifier)
            if identifier in self.terminal and self.terminal[identifier]['phase']=='active':
                if self.terminal[identifier]['meta_checksum']!=hashlib.sha256(dumps(meta)).hexdigest():
                    raise ValueError('Conflicting transfer metadata')
                return self.progress(identifier)
            self._new(identifier)
            if meta['destination_instance']!=self.core.instance or meta['source_instance']==self.core.instance:
                raise ValueError('Wrong destination/source')
            if self.core.algorithms or self.core.revision or self.core.snapshot or self.core.restored_digest or self.core.metrics['failed']:
                raise ValueError('Destination not fresh')
            if self.outgoing:raise ValueError('Sending transfer')
            self.incoming=dict(meta=dict(meta),buffer=bytearray(),candidate=None,phase='receiving')
            self.core.mode='paused'  # cannot accept tasks or legacy restores until activated/cancelled
            return self.progress(identifier)
    def chunk(self,identifier,offset,encoded):
        with self.core.lock:
            entry=self._in(identifier)
            if entry['phase']!='receiving':raise ValueError('Already staged')
            raw=base64.b64decode(encoded,validate=True)
            if not raw or len(raw)>CHUNK_SIZE or offset<0:raise ValueError('Invalid chunk')
            buf=entry['buffer']
            if offset<len(buf):
                if offset+len(raw)>len(buf) or bytes(buf[offset:offset+len(raw)])!=raw:
                    raise ValueError('Conflicting duplicate chunk')
                return self.progress(identifier)
            if offset!=len(buf) or len(buf)+len(raw)>entry['meta']['size']:
                raise ValueError('Out of order or oversized chunk')
            buf.extend(raw)
            return self.progress(identifier)
    def finish(self,identifier):
        with self.core.lock:
            if identifier in self.terminal and self.terminal[identifier]['phase']=='active':return self.progress(identifier)
            entry=self._in(identifier)
            if entry['phase']=='ready':return self.progress(identifier)
            raw=bytes(entry['buffer'])
            if len(raw)!=entry['meta']['size'] or hashlib.sha256(raw).hexdigest()!=entry['meta']['checksum']:
                raise ValueError('Incomplete transfer or checksum mismatch')
            snapshot=check('snapshot',loads(raw))
            if snapshot['handoff_id']!=identifier or snapshot['source_instance']!=entry['meta']['source_instance']:
                raise ValueError('Snapshot identity mismatch')
            candidate=Core(self.core.registry)
            candidate.instance=self.core.instance
            candidate.restore(snapshot)  # validate everything before committing live state
            entry['candidate']=candidate
            entry['phase']='ready'
            return self.progress(identifier)
    def release(self,identifier):
        with self.core.lock:
            entry=self._out(identifier)
            entry['released']=True
            return dict(api_version=API_VERSION,transfer_id=identifier,activation_token=entry['token'])
    def activate(self,identifier,token):
        with self.core.lock:
            hashed=hashlib.sha256(token.encode()).hexdigest()
            if identifier in self.terminal:
                terminal=self.terminal[identifier]
                if terminal['phase']=='active' and secrets.compare_digest(terminal['token_hash'],hashed):
                    return self.progress(identifier)
                raise ValueError('Rejected replay')
            entry=self._in(identifier)
            if entry['phase']!='ready' or not secrets.compare_digest(entry['meta']['token_hash'],hashed):
                raise ValueError('Not ready or invalid release token')
            candidate=entry['candidate']
            self.core.algorithms=candidate.algorithms
            self.core.revision=candidate.revision
            self.core.metrics=dict(candidate.metrics)
            self.core.restored_digest=candidate.restored_digest
            self.core.mode='running'
            self.terminal[identifier]=dict(phase='active',next_offset=entry['meta']['size'],token_hash=hashed,meta_checksum=hashlib.sha256(dumps(entry['meta'])).hexdigest())
            self.incoming=None
            return self.progress(identifier)
    def cancel(self,identifier):
        with self.core.lock:
            if identifier in self.terminal:
                if self.terminal[identifier]['phase']!='cancelled':raise ValueError('Already activated; rollback forbidden')
                return self.progress(identifier)
            entry=self._in(identifier)
            self.terminal[identifier]=dict(phase='cancelled',next_offset=len(entry['buffer']))
            self.incoming=None
            self.core.mode='running'
            return self.progress(identifier)
    def abort(self,identifier):
        with self.core.lock:
            if identifier in self.terminal and self.terminal[identifier]['phase']=='aborted':
                return self.progress(identifier)
            entry=self._out(identifier)
            if entry['released']:raise ValueError('Release is irreversible; retry activation')
            self.terminal[identifier]=dict(phase='aborted',next_offset=0)
            self.outgoing=None
            self.core.snapshot=None
            self.core.mode='running'
            return self.progress(identifier)
    def progress(self,identifier):
        with self.core.lock:
            if identifier in self.terminal:entry=self.terminal[identifier]
            elif self.incoming and self.incoming['meta']['transfer_id']==identifier:
                entry=dict(phase=self.incoming['phase'],next_offset=len(self.incoming['buffer']))
            elif self.outgoing and self.outgoing['meta']['transfer_id']==identifier:
                entry=dict(phase='released' if self.outgoing['released'] else 'prepared',next_offset=0)
            else:raise ValueError('Unknown transfer')
            return dict(api_version=API_VERSION,transfer_id=identifier,phase=entry['phase'],next_offset=entry['next_offset'])
    def _in(self,identifier):
        if not self.incoming or self.incoming['meta']['transfer_id']!=identifier:raise ValueError('No incoming transfer')
        return self.incoming
    def _out(self,identifier):
        if not self.outgoing or self.outgoing['meta']['transfer_id']!=identifier:raise ValueError('No outgoing transfer')
        return self.outgoing
