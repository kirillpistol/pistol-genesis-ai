"""Allowlisted mTLS JSON client. No redirects, no environment proxies."""
import hashlib
import http.client
import ssl
from urllib.parse import urlsplit
from .contracts import dumps, loads, check

MAX_BODY=65536

def origin(url):
    parsed=urlsplit(url)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('','/'):
        raise ValueError('Expected HTTPS origin without credentials/path/query')
    return parsed.hostname,parsed.port or 443

def pins(values):
    if not isinstance(values,list) or not values or any(not isinstance(x,str) or len(x)!=64 or any(c not in '0123456789abcdef' for c in x) for x in values):
        raise ValueError('Expected nonempty lowercase SHA256 certificate pins')
    return set(values)

def context(ca,cert,key):
    ctx=ssl.create_default_context(cafile=ca)
    ctx.minimum_version=ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(cert,key)
    return ctx

class Client:
    def __init__(self, ca, cert, key, peers, timeout=3):
        self.context=context(ca,cert,key)
        self.peers={origin(url):pins(values) for url,values in peers.items()}
        if not 0<timeout<=30:
            raise ValueError('Invalid timeout')
        self.timeout=timeout
    def request(self,url,path,body=None):
        host,port=origin(url)
        if (host,port) not in self.peers:
            raise ValueError('Origin not allowlisted')
        if path not in ('/v1/status','/v1/algorithms/attach','/v1/tasks','/v1/snapshot','/v1/restore'):
            raise ValueError('Unknown route')
        payload=None if body is None else dumps(body)
        if payload is not None and len(payload)>MAX_BODY:
            raise ValueError('Body too large')
        conn=http.client.HTTPSConnection(host,port,context=self.context,timeout=self.timeout)
        try:
            conn.connect() # verify CA, hostname, then pin BEFORE sending payload
            pin=hashlib.sha256(conn.sock.getpeercert(binary_form=True)).hexdigest()
            if pin not in self.peers[(host,port)]:
                raise ValueError('Server identity not allowlisted')
            conn.request('GET' if body is None else 'POST',path,body=payload,
                         headers={'Content-Type':'application/json'})
            response=conn.getresponse()
            if response.status!=200 or response.getheader('Content-Type')!='application/json':
                raise ValueError('Peer rejected request; redirects are not followed')
            raw=response.read(MAX_BODY+1)
            if len(raw)>MAX_BODY:
                raise ValueError('Response too large')
            result=loads(raw)
            schema={'/v1/status':'status','/v1/algorithms/attach':'status','/v1/tasks':'task_response',
                    '/v1/snapshot':'snapshot','/v1/restore':'status'}[path]
            return check(schema,result)
        finally:
            conn.close()
