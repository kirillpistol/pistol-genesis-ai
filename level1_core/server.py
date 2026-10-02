"""Bounded mTLS server; plain HTTP only by explicit localhost development flag."""
import argparse
import hashlib
import importlib
import json
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from .contracts import API_VERSION, check, dumps, loads
from .runtime import Core
from .transfers import Transfers
from .observability import correlation, event, configure
from .transport import MAX_BODY,pins

class Server(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,address,core,tls_context=None,client_pins=None,dev=False,client_roles=None):
        if tls_context is None and (not dev or address[0]!='127.0.0.1'):
            raise ValueError('Plain HTTP is restricted to explicit localhost development')
        if tls_context is not None and not (client_pins or client_roles):
            raise ValueError('Client allowlist required')
        self.core,self.tls_context,self.client_pins=core,tls_context,set(client_pins or [])
        self.client_roles={pin:set(roles) for pin,roles in (client_roles or {}).items()}
        # Legacy configuration remains readable; explicit pins historically had both permissions.
        for pin in client_pins or []:self.client_roles.setdefault(pin,{'control','data'})
        for pin,roles in self.client_roles.items():
            pins([pin])
            if not roles or roles-{'control','data'}:raise ValueError('Invalid client roles')
        self.client_pins=set(self.client_roles)
        self.transfers=Transfers(core)
        self.slots=threading.BoundedSemaphore(8)
        super().__init__(address,Handler)
    def handle_error(self,request,address):
        pass  # no payloads or exception traces in network logs
    def get_request(self):
        sock,address=super().get_request()
        sock.settimeout(3)
        if self.tls_context:
            try:
                sock=self.tls_context.wrap_socket(sock,server_side=True)
                pin=hashlib.sha256(sock.getpeercert(binary_form=True)).hexdigest()
                if pin not in self.client_pins:
                    raise OSError('Client identity not allowlisted')
            except Exception:
                sock.close()
                raise
        return sock,address
    def process_request(self,request,address):
        if not self.slots.acquire(blocking=False):
            body=dumps(dict(api_version=API_VERSION,error='Server busy'))
            try:request.sendall(b'HTTP/1.0 503 Service Unavailable\r\nContent-Type: application/json\r\nRetry-After: 1\r\nContent-Length: '+str(len(body)).encode()+b'\r\n\r\n'+body)
            except OSError:pass
            event('backpressure',status=503,instance=self.core.instance)
            self.shutdown_request(request)
            return
        try:
            super().process_request(request,address)
        except Exception:
            self.slots.release()
            raise
    def process_request_thread(self,request,address):
        try:
            super().process_request_thread(request,address)
        finally:
            self.slots.release()

SAFE_ROUTES={'/v1/status','/v1/tasks','/v1/algorithms/attach','/v1/snapshot','/v1/restore','/v1/health','/v1/readiness'}|{
    '/v1/transfers/'+name for name in ('prepare','begin','download','chunk','progress','finish','release','activate','cancel','abort')}

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def dispatch(self):
        core=self.server.core
        certificate=self.connection.getpeercert(binary_form=True) if self.server.tls_context else None
        pin=hashlib.sha256(certificate).hexdigest() if certificate else None
        roles=self.server.client_roles.get(pin,set()) if pin else {'control','data'}
        self.audit_roles=sorted(roles)
        need='data' if self.path=='/v1/tasks' else 'control'
        if self.path in ('/v1/status','/v1/health','/v1/readiness'):need=None
        if need and need not in roles:
            return 403,dict(api_version=API_VERSION,error='Forbidden role')
        if self.command=='GET' and self.path=='/v1/health':
            return 200,check('health',dict(api_version=API_VERSION,alive=True))
        if self.command=='GET' and self.path=='/v1/readiness':
            with core.lock:
                ready=core.mode=='running' and bool(core.algorithms)
                return (200 if ready else 503),check('readiness',dict(api_version=API_VERSION,ready=ready,reason='ready' if ready else 'paused_or_no_algorithm'))
        if self.command=='GET' and self.path=='/v1/status':return 200,core.status()
        routes={'/v1/algorithms/attach':'attach','/v1/tasks':'task','/v1/snapshot':'snapshot_request','/v1/restore':'snapshot'}
        routes.update({'/v1/transfers/prepare':'snapshot_request','/v1/transfers/begin':'transfer_meta',
                       '/v1/transfers/download':'transfer_offset','/v1/transfers/chunk':'transfer_chunk',
                       '/v1/transfers/progress':'transfer_id','/v1/transfers/finish':'transfer_id',
                       '/v1/transfers/release':'transfer_id','/v1/transfers/activate':'transfer_release',
                       '/v1/transfers/cancel':'transfer_id','/v1/transfers/abort':'transfer_id'})
        if self.command!='POST' or self.path not in routes:
            return 404,dict(api_version=API_VERSION,error='Unknown route')
        if self.headers.get('Transfer-Encoding') or len(self.headers.get_all('Content-Length',[]))!=1:
            raise ValueError('Unsupported framing')
        if self.headers.get('Content-Type')!='application/json':
            raise ValueError('Expected application/json')
        size=int(self.headers['Content-Length'])
        if not 0<size<=MAX_BODY:
            return 413,dict(api_version=API_VERSION,error='Body size limit')
        raw=self.rfile.read(size)
        if len(raw)!=size:raise ValueError('Incomplete body')
        body=check(routes[self.path],loads(raw))
        if self.path.startswith('/v1/transfers/'):
            method=self.path.rsplit('/',1)[1];manager=self.server.transfers
            if method=='prepare':result=manager.prepare(body['destination_instance']);schema='transfer_meta'
            elif method=='begin':result=manager.begin(body);schema='transfer_progress'
            elif method=='download':result=manager.download(body['transfer_id'],body['offset']);schema='transfer_download'
            elif method=='chunk':result=manager.chunk(body['transfer_id'],body['offset'],body['chunk_base64']);schema='transfer_progress'
            elif method=='activate':result=manager.activate(body['transfer_id'],body['activation_token']);schema='transfer_progress'
            else:
                result=getattr(manager,method)(body['transfer_id'])
                schema='transfer_release' if method=='release' else 'transfer_progress'
            return 200,check(schema,result)
        if self.path=='/v1/algorithms/attach':return 200,core.attach(body['name'])
        if self.path=='/v1/tasks':return 200,core.process(body['algorithm'],body['data'])
        if self.path=='/v1/snapshot':return 200,core.export(body['destination_instance'])
        return 200,core.restore(body)
    def handle_request(self):
        started=time.perf_counter()
        request_id=correlation()
        try:
            request_id=correlation(self.headers.get('X-Correlation-ID'))
            code,result=self.dispatch()
            encoded=dumps(result)
        except (ValueError,KeyError,TypeError,OverflowError,RecursionError):
            code,encoded=400,dumps(dict(api_version=API_VERSION,error='Invalid request, version or state'))
        except TimeoutError:
            code,encoded=408,dumps(dict(api_version=API_VERSION,error='Request timeout'))
        except Exception:
            code,encoded=500,dumps(dict(api_version=API_VERSION,error='Internal error'))
        self.send_response(code)
        self.send_header('X-Correlation-ID',request_id)
        self.send_header('X-Error-Code',str(code) if code>=400 else '0')
        if code==503:self.send_header('Retry-After','1')
        self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(encoded)))
        self.end_headers()
        try:self.wfile.write(encoded)
        except OSError:pass
        event('http_request',correlation_id=request_id,method=self.command,
              route=self.path if self.path in SAFE_ROUTES else '<unknown>',status=code,
              roles=getattr(self,'audit_roles',[]),duration_ms=round((time.perf_counter()-started)*1000,3),
              instance=self.server.core.instance)
    do_GET=handle_request
    do_POST=handle_request

def server_context(tls):
    ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version=ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(tls['cert'],tls['key'])
    ctx.load_verify_locations(cafile=tls['ca'])
    ctx.verify_mode=ssl.CERT_REQUIRED
    return ctx

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--host',default='127.0.0.1')
    parser.add_argument('--port',type=int,default=8080)
    parser.add_argument('--tls-config')
    parser.add_argument('--dev-local',action='store_true')
    parser.add_argument('--algorithm-module',help='Explicit trusted installed local module; not downloaded')
    args=parser.parse_args()
    registry=None
    if args.algorithm_module:
        registry=importlib.import_module(args.algorithm_module).REGISTRY
    if args.tls_config:
        with open(args.tls_config,encoding='utf-8') as file:config=json.load(file)
        ctx=server_context(config['tls']);allowed=pins(config['client_pins']) if config.get('client_pins') else None
    else:
        ctx,allowed=None,None
    configure()
    roles=config.get('client_roles') if args.tls_config else None
    server=Server((args.host,args.port),Core(registry),ctx,allowed,args.dev_local,roles)
    event('server_started',host=args.host,port=args.port,api_version=API_VERSION,instance=server.core.instance)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()

if __name__=='__main__':main()
