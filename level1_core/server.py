"""Bounded mTLS server; plain HTTP only by explicit localhost development flag."""
import argparse
import hashlib
import importlib
import json
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from .contracts import API_VERSION, check, dumps, loads
from .runtime import Core
from .transport import MAX_BODY,pins

class Server(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,address,core,tls_context=None,client_pins=None,dev=False):
        if tls_context is None and (not dev or address[0]!='127.0.0.1'):
            raise ValueError('Plain HTTP is restricted to explicit localhost development')
        if tls_context is not None and not client_pins:
            raise ValueError('Client allowlist required')
        self.core,self.tls_context,self.client_pins=core,tls_context,set(client_pins or [])
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

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def dispatch(self):
        core=self.server.core
        if self.command=='GET' and self.path=='/v1/status':return 200,core.status()
        routes={'/v1/algorithms/attach':'attach','/v1/tasks':'task','/v1/snapshot':'snapshot_request','/v1/restore':'snapshot'}
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
        if self.path=='/v1/algorithms/attach':return 200,core.attach(body['name'])
        if self.path=='/v1/tasks':return 200,core.process(body['algorithm'],body['data'])
        if self.path=='/v1/snapshot':return 200,core.export(body['destination_instance'])
        return 200,core.restore(body)
    def handle_request(self):
        try:
            code,result=self.dispatch()
            encoded=dumps(result)
        except (ValueError,KeyError,TypeError,OverflowError,RecursionError):
            code,encoded=400,dumps(dict(api_version=API_VERSION,error='Invalid request, version or state'))
        except TimeoutError:
            code,encoded=408,dumps(dict(api_version=API_VERSION,error='Request timeout'))
        except Exception:
            code,encoded=500,dumps(dict(api_version=API_VERSION,error='Internal error'))
        self.send_response(code)
        self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(encoded)))
        self.end_headers()
        try:self.wfile.write(encoded)
        except OSError:pass
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
        ctx=server_context(config['tls']);allowed=pins(config['client_pins'])
    else:
        ctx,allowed=None,None
    server=Server((args.host,args.port),Core(registry),ctx,allowed,args.dev_local)
    print(f'GENESIS API 1.0 listening on {args.host}:{args.port}',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()

if __name__=='__main__':main()
