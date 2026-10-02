"""Exercise frozen v1 client/new server and new client/frozen server over real mTLS.

Each participant runs in a separate process with its own import paths. No shared imports.
"""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

CLIENT_CODE='''
import json,sys
from level1_core.transport import Client
from level3_data.adapter import Adapter
config=json.load(open(sys.argv[1]))
tls=config['tls']
client=Client(tls['ca'],tls['cert'],tls['key'],config['peers'])
source,destination=sys.argv[2:4]
v='1.0'
for name in ('numeric','evaluator'):
    client.request(source,'/v1/algorithms/attach',dict(api_version=v,name=name))
adapter=Adapter(client,source,'numeric')
for x in (10,20,30):adapter.send(x)
target=client.request(destination,'/v1/status')
snapshot=client.request(source,'/v1/snapshot',dict(api_version=v,destination_instance=target['instance']))
client.request(destination,'/v1/restore',snapshot)
result=Adapter(client,destination,'numeric').send(40)
assert result['result']['count']==4 and result['result']['mean']==25
quality=Adapter(client,destination,'evaluator')
quality.send(dict(prediction=2,target=1))
result=quality.send(dict(prediction=2,target=3))
assert result['result']['mae']==1 and result['result']['rmse']==1
print('PASS wire v1.0: numeric, external adapter, snapshot and evaluation')
'''

def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));return sock.getsockname()[1]

def main():
    parser=argparse.ArgumentParser()
    for name in ('current-core','current-adapter','current-algorithms','baseline-core','baseline-adapter','baseline-algorithms'):
        parser.add_argument('--'+name,required=True,type=Path)
    parser.add_argument('--profile',choices=['old-client','old-server'],required=True)
    args=parser.parse_args()
    current=args.current_core.resolve();baseline=args.baseline_core.resolve()
    client_core=baseline if args.profile=='old-client' else current
    client_adapter=(args.baseline_adapter if args.profile=='old-client' else args.current_adapter).resolve()
    server_core=current if args.profile=='old-client' else baseline
    algorithms=(args.current_algorithms if args.profile=='old-client' else args.baseline_algorithms).resolve()
    # Import generator from current core only in coordinator. Participants are separate subprocesses.
    sys.path.insert(0,str(current))
    from examples.make_test_certs import generate
    processes=[]
    with tempfile.TemporaryDirectory() as temp:
        root=generate(temp)
        server_config=json.loads((root/'server.json').read_text())
        client_config=json.loads((root/'client.json').read_text())
        # Frozen server accepts legacy client_pins only. Current server accepts the same explicit principals.
        client_pin=next(iter(server_config['client_roles']))
        server_config.pop('client_roles');server_config['client_pins']=[client_pin]
        (root/'server.json').write_text(json.dumps(server_config))
        pin=next(iter(client_config['peers'].values()))
        ports=[port(),port()]
        while ports[0]==ports[1]:ports[1]=port()
        urls=[f'https://localhost:{p}' for p in ports]
        client_config['peers']={url:pin for url in urls}
        (root/'client.json').write_text(json.dumps(client_config))
        server_env=dict(os.environ,PYTHONPATH=str(algorithms))
        try:
            for p in ports:
                log=open(root/(str(p)+'.log'),'w')
                process=subprocess.Popen([sys.executable,'-m','level1_core.server','--port',str(p),'--tls-config',str(root/'server.json'),'--algorithm-module','level2_algorithms.numeric'],cwd=server_core,env=server_env,stdout=log,stderr=log)
                log.close();processes.append(process)
                deadline=time.monotonic()+10
                while time.monotonic()<deadline:
                    if process.poll() is not None:raise RuntimeError('Participant startup failed: '+(root/(str(p)+'.log')).read_text())
                    try:
                        with socket.create_connection(('127.0.0.1',p),timeout=.1):break
                    except OSError:time.sleep(.05)
                else:raise RuntimeError('Startup timeout')
            client_env=dict(os.environ,PYTHONPATH=str(client_adapter))
            subprocess.run([sys.executable,'-c',CLIENT_CODE,str(root/'client.json'),*urls],cwd=client_core,env=client_env,check=True,timeout=30)
            print('PASS compatibility profile:',args.profile)
        finally:
            for process in processes:process.terminate()
            for process in processes:
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired:process.kill();process.wait()

if __name__=='__main__':main()
