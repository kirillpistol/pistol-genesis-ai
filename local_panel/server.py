"""Loopback-only operator panel. Random capability, exact Origin and Host required."""
import argparse
import hmac
import json
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import secrets
import threading
import webbrowser
from .manager import Manager

class App:
    def __init__(self):
        self.manager=Manager();self.lock=threading.Lock();self.busy=False;self.error=None;self.last_action=None
    def action(self,name,values=None):
        with self.lock:
            if self.busy:raise ValueError('Операция уже выполняется')
            if name not in ('start','step','finish','move','test','stop'):raise ValueError('Неизвестная операция')
            self.busy=True;self.error=None;self.last_action=name
        def work():
            try:
                with self.lock:
                    m=self.manager
                    if name=='start':m.start(values)
                    elif name=='step':m.consume(1)
                    elif name=='finish':m.consume()
                    elif name=='move':m.move()
                    elif name=='test':m.test()
                    elif name=='stop':m.stop();m.event('Окружение остановлено. Временные ключи и файлы удалены.')
            except Exception as error:
                with self.lock:self.error=str(error);self.manager.event('Ошибка: '+str(error))
            finally:
                with self.lock:self.busy=False
        threading.Thread(target=work,daemon=True).start()
    def state(self):
        if not self.lock.acquire(blocking=False):return dict(busy=True)
        try:return dict(**self.manager.snapshot(),busy=self.busy,error=self.error,last_action=self.last_action)
        finally:self.lock.release()

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def reply(self,status,value,content_type='application/json; charset=utf-8'):
        raw=json.dumps(value,ensure_ascii=False).encode() if content_type.startswith('application/json') else value
        self.send_response(status);self.send_header('Content-Type',content_type);self.send_header('Content-Length',str(len(raw)))
        self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers();self.wfile.write(raw)
    def valid(self,write=False):
        if self.headers.get('Host')!=self.server.address:return False
        if not hmac.compare_digest(self.headers.get('X-Genesis-Token',''),self.server.token):return False
        return not write or self.headers.get('Origin')=='http://'+self.server.address
    def do_GET(self):
        if self.headers.get('Host')!=self.server.address:self.reply(403,dict(error='Host rejected'));return
        if self.path=='/api/state':
            if not self.valid():self.reply(403,dict(error='Token required'));return
            self.reply(200,self.server.app.state());return
        files={'/':'index.html','/app.js':'app.js','/style.css':'style.css'}
        if self.path not in files:self.reply(404,dict(error='Not found'));return
        name=files[self.path];raw=(Path(__file__).parent/'static'/name).read_bytes()
        kind={'html':'text/html; charset=utf-8','js':'text/javascript; charset=utf-8','css':'text/css; charset=utf-8'}[name.split('.')[-1]]
        self.reply(200,raw,kind)
    def do_POST(self):
        if not self.valid(True):self.reply(403,dict(error='Origin or token rejected'));return
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=8192:raise ValueError('Request size')
            if self.headers.get('Content-Type')!='application/json':raise ValueError('JSON required')
            body=json.loads(self.rfile.read(size))
            if self.path!='/api/action' or type(body) is not dict or set(body)-{'action','values'}:raise ValueError('Invalid request')
            self.server.app.action(body['action'],body.get('values'));self.reply(202,dict(accepted=True))
        except (ValueError,KeyError,TypeError) as error:self.reply(400,dict(error=str(error)))

class Server(ThreadingHTTPServer):
    daemon_threads=True
    def get_request(self):
        sock,address=super().get_request();sock.settimeout(3);return sock,address

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8765);parser.add_argument('--no-browser',action='store_true');args=parser.parse_args()
    server=Server(('127.0.0.1',args.port),Handler);server.address='127.0.0.1:'+str(server.server_port)
    server.token=secrets.token_urlsafe(32);server.app=App()
    url='http://'+server.address+'/#'+server.token
    print('PISTOL GENESIS local panel:',url,flush=True)
    if not args.no_browser:webbrowser.open(url)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:
        server.server_close()
        with server.app.lock:server.app.manager.stop()
if __name__=='__main__':main()
