"""Repeated probes of configured allowlisted origins; never scans the network."""
import argparse
import http.client
import json
import random
import threading
import time
from .observability import event
from .transport import Client

class Discovery:
    def __init__(self,client,endpoints,initial=1,maximum=30):
        if not 0<initial<=maximum<=300:
            raise ValueError('Invalid retry bounds')
        if not endpoints or len(endpoints)>32:
            raise ValueError('Expected 1..32 configured endpoints')
        self.client,self.endpoints=client,tuple(endpoints)
        self.initial,self.maximum=initial,maximum
        self.lock=threading.Lock()
        self.report={'state':'searching','attempts':0,'available':[],'next_retry_s':0,'errors':[],'checked_at':None}
        self.ttl=maximum
    def probe_once(self):
        available=[]
        errors=[]
        for endpoint in self.endpoints:
            try:
                result=self.client.request(endpoint,'/v1/status')
                if result['mode']=='running':
                    available.append({'endpoint':endpoint,'instance':result['instance']})
            except (OSError,ValueError,http.client.HTTPException) as error:
                errors.append({'endpoint':endpoint,'kind':type(error).__name__})
        with self.lock:
            self.report.update(state='connected' if available else 'waiting_for_allowed_server',
                               attempts=self.report['attempts']+1,available=available,errors=errors,checked_at=time.monotonic())
        return available
    def status(self):
        with self.lock:
            result=dict(self.report)
            if result['checked_at'] is not None and time.monotonic()-result['checked_at']>self.ttl:
                result.update(state='stale',available=[])
            return result
    def choose(self,exclude=(),algorithm=None):
        # Revalidate instead of trusting cached results; task delivery can still fail afterwards.
        for candidate in self.probe_once():
            if candidate['endpoint'] in exclude:continue
            try:
                status=self.client.request(candidate['endpoint'],'/v1/status')
                if status['mode']=='running' and status['instance']==candidate['instance'] and (algorithm is None or algorithm in status['algorithms']):
                    return candidate
            except (OSError,ValueError,http.client.HTTPException):pass
        raise ValueError('No live compatible allowlisted node')
    def run(self,stop,on_status=None):
        delay=self.initial
        while not stop.is_set():
            found=self.probe_once()
            wait=min(self.maximum,delay*random.uniform(.8,1.2))
            with self.lock:
                self.report['next_retry_s']=wait
            event('discovery_probe',attempt=self.status()['attempts'],available_count=len(found),next_retry_s=wait)
            if on_status:
                on_status(self.status())
            if stop.wait(wait):
                break
            delay=self.initial if found else min(self.maximum,delay*2)

def client_from(config):
    tls=config['tls']
    return Client(tls['ca'],tls['cert'],tls['key'],config['peers'],config.get('timeout_s',3))

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',required=True)
    args=parser.parse_args()
    with open(args.config,encoding='utf-8') as file:
        config=json.load(file)
    client=client_from(config)
    discovery=Discovery(client,config['endpoints'],config.get('initial_retry_s',1),config.get('max_retry_s',30))
    stop=threading.Event()
    try:
        discovery.run(stop,lambda status:print(json.dumps(status),flush=True))
    except KeyboardInterrupt:
        stop.set()

if __name__=='__main__':main()
