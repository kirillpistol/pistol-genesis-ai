"""Repeated probes of configured allowlisted origins; never scans the network."""
import argparse
import http.client
import json
import random
import threading
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
        self.report={'state':'searching','attempts':0,'available':[],'next_retry_s':0}
    def probe_once(self):
        available=[]
        for endpoint in self.endpoints:
            try:
                result=self.client.request(endpoint,'/v1/status')
                if result['mode']=='running':
                    available.append({'endpoint':endpoint,'instance':result['instance']})
            except (OSError,ValueError,http.client.HTTPException):
                pass
        with self.lock:
            self.report.update(state='connected' if available else 'waiting_for_allowed_server',
                               attempts=self.report['attempts']+1,available=available)
        return available
    def status(self):
        with self.lock:
            return dict(self.report)
    def run(self,stop,on_status=None):
        delay=self.initial
        while not stop.is_set():
            found=self.probe_once()
            wait=min(self.maximum,delay*random.uniform(.8,1.2))
            with self.lock:
                self.report['next_retry_s']=wait
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
