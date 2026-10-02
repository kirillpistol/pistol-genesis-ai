"""Allowlisted mTLS JSON client. No redirects, no environment proxies."""
import hashlib
import http.client
import ssl
from .observability import correlation
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

class PeerError(ValueError):
    def __init__(self,status,correlation_id=None):
        super().__init__('Peer rejected request')
        self.status=status
        self.correlation_id=correlation_id
        self.retryable=status in (408,429,500,502,503,504)

class Client:
    def __init__(self, ca, cert, key, peers, timeout=3):
        self.context=context(ca,cert,key)
        self.peers={origin(url):pins(values) for url,values in peers.items()}
        if not 0<timeout<=30:
            raise ValueError('Invalid timeout')
        self.timeout=timeout
    def request(self,url,path,body=None,correlation_id=None):
        request_id=correlation(correlation_id)
        host,port=origin(url)
        if (host,port) not in self.peers:
            raise ValueError('Origin not allowlisted')
        if path not in RESPONSE_SCHEMAS:
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
                         headers={'Content-Type':'application/json','X-Correlation-ID':request_id})
            response=conn.getresponse()
            if response.status!=200:
                raise PeerError(response.status,response.getheader('X-Correlation-ID'))
            if response.getheader('Content-Type')!='application/json':
                raise ValueError('Expected JSON; redirects are not followed')
            raw=response.read(MAX_BODY+1)
            if len(raw)>MAX_BODY:
                raise ValueError('Response too large')
            result=loads(raw)
            schema=RESPONSE_SCHEMAS[path]
            return check(schema,result)
        finally:
            conn.close()

RESPONSE_SCHEMAS={'/v1/status':'status','/v1/algorithms/attach':'status','/v1/tasks':'task_response',
                  '/v1/snapshot':'snapshot','/v1/restore':'status','/v1/health':'health','/v1/readiness':'readiness'}
RESPONSE_SCHEMAS.update({'/v1/transfers/'+method:schema for method,schema in {
    'prepare':'transfer_meta','begin':'transfer_progress','download':'transfer_download',
    'chunk':'transfer_progress','progress':'transfer_progress','finish':'transfer_progress',
    'release':'transfer_release','activate':'transfer_progress','cancel':'transfer_progress','abort':'transfer_progress'}.items()})

RESPONSE_SCHEMAS.update({'/v1/bindings/select':'binding_status','/v1/bindings/status':'binding_status',
                         '/v1/bindings/catalog':'binding_catalog','/v1/bindings/tasks':'task_response',
                         '/v1/bindings/close':'status'})
